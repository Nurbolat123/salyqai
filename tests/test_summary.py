from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from salyq.auth import consents
from salyq.categorize import service
from salyq.models import RegionRate, TaxCalculation
from salyq.tax import config_store
from salyq.tax.config import RATES_DIR
from salyq.tax.summary import PeriodError, explain, parse_period, tax_summary
from tests.helpers import make_kz_id, make_user
from tests.test_categorize import import_rows

V = dict(consents.CONSENT_VERSIONS)
ALMATY = "750000000"


@pytest.fixture
def user(db):
    u = make_user(db)
    u.region_code = ALMATY
    db.commit()
    for t in ("pd_processing", "automated_processing"):
        consents.grant(db, u, t, V[t])
    return u


def confirm_all(db, user, txs, category="income_sales"):
    for t in txs:
        service.confirm(db, user, t, category=category)


class TestPeriod:
    def test_parse(self):
        p = parse_period("2026H2")
        assert (p.year, p.half, p.start, p.end) == (2026, 2, date(2026, 7, 1), date(2026, 12, 31))
        with pytest.raises(PeriodError):
            parse_period("2026Q1")


class TestSummary:
    def test_only_confirmed_income_is_taxed(self, db, user):
        txs = import_rows(db, user, [
            ["01.07.2026", "1", "", "1 000 000", "ТОО А", "Оплата по договору"],
            ["02.07.2026", "2", "", "500 000", "ТОО Б", "Оплата по договору"],
            ["03.07.2026", "3", "", "300 000", "Х", "Перевод"],
            ["04.07.2026", "4", "", "200 000", "Сестра", "Перевод"],
        ])
        confirm_all(db, user, txs[:2])
        service.confirm(db, user, txs[3], category="personal_transfer")
        s = tax_summary(db, user, parse_period("2026H2"), today=date(2026, 10, 6))
        assert s["income_tiyn"] == 150_000_000
        assert s["tax"]["total_payable_tiyn"] == 6_000_000  # 4% базовая ставка
        assert s["pending"] == {"count": 1, "amount_tiyn": 30_000_000, "possible_tax_tiyn": 1_200_000}
        assert s["piggybank"]["per_100000_tenge_tiyn"] == 400_000
        assert s["piggybank"]["reserve_tiyn"] == 7_200_000
        codes = [w["code"] for w in s["warnings"]]
        assert "REGION_RATE_MISSING" in codes and "UNCONFIRMED_INCOME" in codes
        assert s["deadlines"]["declaration_910"] == "2027-02-15"
        assert s["deadlines"]["social_payments_next"] == "2026-10-25"

    def test_region_rate_and_override(self, db, user):
        db.add(RegionRate(region_code=ALMATY, rate=Decimal("0.03"), valid_from=date(2026, 1, 1), source_url="u"))
        db.commit()
        a, b = import_rows(db, user, [
            ["01.02.2026", "1", "", "1 000 000", "А", "Оплата по договору"],
            ["01.03.2026", "2", "", "1 000 000", "Б", "Оплата по договору"],
        ])
        service.confirm(db, user, a, category="income_sales")
        service.confirm(db, user, b, category="income_sales", region_code="710000000")
        s = tax_summary(db, user, parse_period("2026H1"))
        assert {r["region_code"]: r["tax_tiyn"] for r in s["regions"]} == {ALMATY: 3_000_000, "710000000": 4_000_000}

    def test_h2_uses_h1_for_annual_thresholds(self, db, user):
        (h1,) = import_rows(db, user, [["01.02.2026", "1", "", "43 000 000", "А", "Оплата по договору"]])
        (h2,) = import_rows(db, user, [["01.08.2026", "2", "", "300 000", "Б", "Оплата по договору"]])
        confirm_all(db, user, [h1, h2])
        codes = [w["code"] for w in tax_summary(db, user, parse_period("2026H2"))["warnings"]]
        assert "VAT_THRESHOLD_EXCEEDED" in codes

    def test_social_uses_declared_income(self, db, user):
        user.declared_income_tiyn = 20_000_000
        db.commit()
        s = tax_summary(db, user, parse_period("2026H2"))
        assert s["social_monthly"]["payments_tiyn"]["opv"] == 2_000_000
        assert s["social_half_year_tiyn"] == s["social_monthly"]["total_tiyn"] * 6

    def test_missing_region_warning(self, db):
        u = make_user(db, iin=make_kz_id("90020240012"))
        codes = [w["code"] for w in tax_summary(db, u, parse_period("2026H2"))["warnings"]]
        assert codes[0] == "PROFILE_REGION_MISSING"

    def test_calculation_saved_once_per_inputs(self, db, user):
        (tx,) = import_rows(db, user, [["01.07.2026", "1", "", "100 000", "А", "Оплата по договору"]])
        p = parse_period("2026H2")
        first = tax_summary(db, user, p)["calculation_id"]
        assert tax_summary(db, user, p)["calculation_id"] == first
        service.confirm(db, user, tx, category="income_sales")
        second = tax_summary(db, user, p)["calculation_id"]
        assert second != first
        assert len(db.scalars(select(TaxCalculation)).all()) == 2

    def test_explain(self, db, user):
        (tx,) = import_rows(db, user, [["01.07.2026", "1", "", "100 000", "ТОО Ромашка", "Оплата по договору"]])
        service.confirm(db, user, tx, category="income_sales")
        calc = db.get(TaxCalculation, tax_summary(db, user, parse_period("2026H2"))["calculation_id"])
        e = explain(db, user, calc)
        assert e["key_factors"][0].startswith("Доход за 2026H2: 100 000,00 ₸ — сумма 1 подтверждённых")
        assert e["operations"][0]["counterparty"] == "ТОО Ромашка"
        assert "Возразить" in e["rights"]
        assert any("не утверждены" in f for f in e["key_factors"])


class TestConfigStore:
    def test_repo_file_is_never_approved(self, db):
        assert config_store.active_config(db, 2026).config.approved_by is None

    def test_submit_and_approve(self, db, user):
        expert = make_user(db, iin=make_kz_id("90020240012"), full_name="Эксперт Бухгалтер")
        raw = (RATES_DIR / "2026.yaml").read_text(encoding="utf-8").replace('version: "2026.1"', 'version: "2026.2"').replace(
            'rate: "0.04"', 'rate: "0.03"')
        row = config_store.submit(db, expert, raw)
        assert config_store.active_config(db, 2026).config.version == "2026.1"  # черновик ещё не действует
        config_store.approve(db, expert, row.id)
        active = config_store.active_config(db, 2026)
        assert (active.config.version, active.config.approved_by) == ("2026.2", "Эксперт Бухгалтер")
        assert active.config.simplified.rate == Decimal("0.03")
        s = tax_summary(db, user, parse_period("2026H2"))
        assert s["config"] == {"year": 2026, "version": "2026.2", "approved_by": "Эксперт Бухгалтер"}
        assert "CONFIG_NOT_APPROVED" not in [w["code"] for w in s["warnings"]]
        with pytest.raises(config_store.ConfigStoreError, match="уже есть"):
            config_store.submit(db, expert, raw)

    def test_invalid_upload(self, db, user):
        with pytest.raises(config_store.ConfigStoreError):
            config_store.submit(db, user, "year: 2026\n")


class TestApi:
    def test_summary_and_explain(self, client):
        from tests.test_kaspi_statement import FIXTURE

        client.patch("/api/v1/me", json={"region_code": ALMATY})
        client.post("/api/v1/consents", json={"type": "automated_processing", "version": V["automated_processing"]})
        client.post("/api/v1/statements", files={"file": ("f.csv", FIXTURE.read_bytes(), "text/csv")})
        client.post("/api/v1/transactions/confirm-suggested", json={})
        s = client.get("/api/v1/tax/summary", params={"period": "2026H1"}).json()
        assert s["income_tiyn"] == 15_000_000 + 2 * 2_550_050
        e = client.get(f"/api/v1/tax/calculations/{s['calculation_id']}/explain").json()
        assert len(e["operations"]) == 3
        assert client.get("/api/v1/tax/summary", params={"period": "bad"}).status_code == 422
        assert client.get("/api/v1/tax/calculations/999/explain").status_code == 404
