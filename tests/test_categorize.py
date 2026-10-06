from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from salyq.auth import consents
from salyq.categorize import service
from salyq.categorize.rules import Suggestion, TxFacts, apply_rules, load_rules
from salyq.fx import FxError, parse_nbk_rates, store_rates, to_kzt_tiyn
from salyq.models import AuditLog, CategorizationRule, Transaction
from salyq.statements import parse_kaspi_statement
from salyq.statements.repository import save_statement
from tests.helpers import make_kz_iban, make_kz_id, make_user
from tests.test_kaspi_statement import FIXTURE, HEADER, rows_csv

V = dict(consents.CONSENT_VERSIONS)


def facts(direction="in", knp="", text="", own=False):
    return TxFacts(direction, knp, text.lower(), own)


class TestRules:
    def test_rules_file_is_valid(self):
        assert load_rules().rules

    @pytest.mark.parametrize(
        ("f", "category", "min_conf"),
        [
            (facts("in", text="Продажи Kaspi QR"), "income_sales", 0.85),
            (facts("in", text="Оплата по счёту № 12 за услуги"), "income_sales", 0.85),
            (facts("in", text="Возврат займа"), "loan", 0.85),
            (facts("in", text="Возврат оплаты за товар"), "refund", 0.85),
            (facts("out", knp="010", text="ОПВ за январь"), "social_payments", 0.95),
            (facts("out", knp="911", text=""), "taxes", 0.95),
            (facts("out", text="Комиссия за обслуживание"), "other_expense", 0.9),
            (facts("in", text="Перевод собственных средств"), "own_transfer", 0.85),
            (facts("in", own=True), "own_transfer", 0.95),
        ],
    )
    def test_known(self, f, category, min_conf):
        s = apply_rules(f)
        assert s.category == category and s.confidence >= min_conf

    def test_unknown_income_goes_to_review(self):
        s = apply_rules(facts("in", text="Перевод"))
        assert (s.category, s.source) == ("income_sales", "rule:default_in")
        assert s.confidence < 0.85

    def test_direction_respected(self):
        # «налог» во входящем — не налоговый платёж
        assert apply_rules(facts("in", text="возврат излишне уплаченного налога")).category == "refund"


class FakeClassifier:
    def __init__(self, suggestion):
        self.suggestion, self.calls = suggestion, []

    def classify(self, *, direction, knp, text):
        self.calls.append(text)
        return self.suggestion


@pytest.fixture
def user(db):
    u = make_user(db)
    consents.grant(db, u, "pd_processing", V["pd_processing"])
    consents.grant(db, u, "automated_processing", V["automated_processing"])
    return u


def import_rows(db, user, rows, header=HEADER, classifier=None):
    data = rows_csv(rows, header=header)
    r = save_statement(db, parse_kaspi_statement(data, "a.csv"), user_id=user.id, raw=data, filename="a.csv")
    txs = db.scalars(select(Transaction).where(Transaction.id.in_(r.inserted_ids)).order_by(Transaction.id)).all()
    service.categorize(db, user, txs, classifier)
    return txs


class TestService:
    def test_fixture_statement(self, db, user):
        data = FIXTURE.read_bytes()
        save_statement(db, parse_kaspi_statement(data, "f.csv"), user_id=user.id, raw=data, filename="f.csv")
        service.categorize(db, user)
        txs = db.scalars(service.user_transactions(user).order_by(Transaction.id)).all()
        got = [(t.purpose[:20], t.category, service.status_of(t)) for t in txs]
        assert got == [
            ("Оплата по счёту № 12", "income_sales", "suggested"),
            ("Продажи Kaspi QR", "income_sales", "suggested"),
            ("Продажи Kaspi QR", "income_sales", "suggested"),
            # контрагент с ИИН самого пользователя → перевод между своими
            ("Возврат займа", "own_transfer", "suggested"),
            ("Комиссия за обслужив", "other_expense", "suggested"),
        ]

    def test_no_consent_means_manual(self, db):
        u = make_user(db)
        (tx,) = import_rows(db, u, [["01.01.2026", "1", "", "100", "А", "Продажи Kaspi QR"]])
        assert (tx.category, service.status_of(tx), service.review_reason(tx)) == (None, "review", "no_consent")

    def test_llm_used_only_below_threshold(self, db, user):
        clf = FakeClassifier(Suggestion("personal_transfer", 0.9, "llm"))
        tx1, tx2 = import_rows(db, user, [
            ["01.01.2026", "1", "", "100", "А", "Перевод"],
            ["01.01.2026", "2", "", "100", "Б", "Продажи Kaspi QR"],
        ], classifier=clf)
        assert (tx1.category, tx1.category_source) == ("personal_transfer", "llm")
        assert tx2.category_source == "rule:kw_sales"
        assert clf.calls == ["Перевод"]

    def test_llm_failure_keeps_rule(self, db, user):
        (tx,) = import_rows(db, user, [["01.01.2026", "1", "", "100", "А", "Перевод"]], classifier=FakeClassifier(None))
        assert (tx.category, service.status_of(tx)) == ("income_sales", "review")

    def test_confirm_creates_personal_rule_and_applies_it(self, db, user):
        rows = [["0%d.01.2026" % d, str(d), "", "100", "Сестра Айгуль", "Перевод"] for d in (1, 2, 3)]
        tx1, tx2, tx3 = import_rows(db, user, rows)
        assert {service.status_of(t) for t in (tx1, tx2, tx3)} == {"review"}
        service.confirm(db, user, tx1, category="personal_transfer")
        db.refresh(tx2)
        assert (tx2.category, tx2.category_source, service.status_of(tx2)) == ("personal_transfer", "personal_rule", "suggested")
        rule = db.scalar(select(CategorizationRule))
        assert (rule.category, rule.confirmations) == ("personal_transfer", 1)
        service.confirm(db, user, tx2, category="personal_transfer")
        assert db.scalar(select(CategorizationRule)).confirmations == 2
        db.refresh(tx3)
        assert float(tx3.confidence) == 0.99
        entry = db.scalar(select(AuditLog).where(AuditLog.action == "transaction.confirm"))
        assert entry.details == {"previous": "income_sales", "category": "personal_transfer"}

    def test_changing_mind_resets_rule(self, db, user):
        tx1, tx2 = import_rows(db, user, [["01.01.2026", str(d), "", "100", "Х", "Перевод"] for d in (1, 2)])
        service.confirm(db, user, tx1, category="personal_transfer")
        service.confirm(db, user, tx2, category="income_sales")
        rule = db.scalar(select(CategorizationRule))
        assert (rule.category, rule.confirmations) == ("income_sales", 1)

    def test_confirm_validation(self, db, user):
        (tx,) = import_rows(db, user, [["01.01.2026", "1", "", "100", "А", "x"]])
        with pytest.raises(service.CategorizeError, match="невозможна"):
            service.confirm(db, user, tx, category="taxes")  # налог — только списание
        with pytest.raises(service.CategorizeError, match="неизвестная"):
            service.confirm(db, user, tx, category="nope")

    def test_bulk_confirm_only_suggested(self, db, user):
        txs = import_rows(db, user, [
            ["01.01.2026", "1", "", "100", "А", "Продажи Kaspi QR"],
            ["01.01.2026", "2", "", "100", "Б", "Перевод"],
        ])
        assert service.confirm_suggested(db, user) == 1
        assert [service.status_of(t) for t in txs] == ["confirmed", "review"]
        assert db.scalar(select(CategorizationRule)) is None

    def test_own_account_by_iban(self, db, user):
        other_own = make_kz_iban("601", "B000000000001")
        data = rows_csv([["01.01.2026", "1", "", "100", "x", "y"]], preamble=(f"Счёт: {other_own}",))
        save_statement(db, parse_kaspi_statement(data, "o.csv"), user_id=user.id, raw=data, filename="o.csv")
        (tx,) = import_rows(db, user, [["01.01.2026", "1", "", "100", "ИП", "Перевод", other_own]],
                            header=HEADER + ["Счёт контрагента"])
        assert (tx.category, tx.category_source) == ("own_transfer", "own_account")

    def test_user_isolation(self, db, user):
        (tx,) = import_rows(db, user, [["01.01.2026", "1", "", "100", "А", "x"]])
        stranger = make_user(db, iin=make_kz_id("90020240012"))
        assert service.get_user_transaction(db, stranger, tx.id) is None


class TestFx:
    XML = """<?xml version="1.0" encoding="utf-8"?><rates><date>15.01.2026</date>
      <item><title>USD</title><description>505.12</description><quant>1</quant></item>
      <item><title>KRW</title><description>3.62</description><quant>10</quant></item></rates>"""

    def test_parse(self):
        assert parse_nbk_rates(self.XML) == {"USD": Decimal("505.120000"), "KRW": Decimal("0.362000")}
        with pytest.raises(FxError):
            parse_nbk_rates("<rates/>")

    def test_conversion(self):
        assert to_kzt_tiyn(10_050, Decimal("505.12")) == 5_076_456  # 100,50 USD → 50 764,56 ₸

    def test_foreign_income_needs_rate(self, db, user):
        hdr = HEADER + ["Валюта"]
        (tx,) = import_rows(db, user, [["15.01.2026", "1", "", "100", "Client LLC", "Payment for services", "USD"]], header=hdr)
        assert (service.status_of(tx), service.review_reason(tx)) == ("review", "no_fx_rate")
        with pytest.raises(service.CategorizeError, match="курса"):
            service.confirm(db, user, tx, category="income_sales")
        store_rates(db, date(2026, 1, 15), parse_nbk_rates(self.XML))
        service.categorize(db, user)
        assert (tx.fx_rate, tx.amount_kzt_tiyn) == (Decimal("505.12"), 5_051_200)
        service.confirm(db, user, tx, category="income_sales")
        assert service.status_of(tx) == "confirmed"


class TestApi:
    def test_queue_and_confirm(self, client):
        files = {"file": (FIXTURE.name, FIXTURE.read_bytes(), "text/csv")}
        assert client.post("/api/v1/statements", files=files).status_code == 200
        body = client.get("/api/v1/transactions").json()
        assert body["counts"] == {"review": 5, "suggested": 0, "confirmed": 0}  # нет согласия ст. 19-1
        assert {i["review_reason"] for i in body["items"]} == {"no_consent"}

        client.post("/api/v1/consents", json={"type": "automated_processing", "version": V["automated_processing"]})
        assert client.post("/api/v1/transactions/recategorize").json() == {"processed": 5}
        counts = client.get("/api/v1/transactions").json()["counts"]
        assert counts["review"] + counts["suggested"] == 5 and counts["suggested"] >= 4

        review = client.get("/api/v1/transactions", params={"status": "suggested"}).json()["items"]
        tx_id = review[0]["id"]
        r = client.patch(f"/api/v1/transactions/{tx_id}", json={"category": "refund"})
        assert r.status_code == 200 and r.json()["status"] == "confirmed"
        assert client.patch(f"/api/v1/transactions/{tx_id}", json={"category": "nope"}).status_code == 422
        assert client.patch("/api/v1/transactions/999999", json={"category": "refund"}).status_code == 404
        n = client.post("/api/v1/transactions/confirm-suggested", json={}).json()["confirmed"]
        assert client.get("/api/v1/transactions").json()["counts"]["confirmed"] == n + 1
