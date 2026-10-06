import io
from datetime import date, datetime
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import select, text

from salyq.models import AuditLog, BankAccount, Statement, Transaction, User
from salyq.statements import StatementParseError, parse_kaspi_statement, parse_rows
from salyq.statements.repository import save_statement
from tests.helpers import make_kz_iban, make_kz_id, make_user

FIXTURE = Path(__file__).parent / "fixtures" / "kaspi_business_2026_01.csv"
PDF_FIXTURE = FIXTURE.with_suffix(".pdf")
ACCOUNT = make_kz_iban()

HEADER = ["Дата операции", "Номер документа", "Дебет", "Кредит", "Контрагент", "Назначение платежа"]


def rows_csv(rows: list[list[str]], header=HEADER, preamble=(f"Счёт: {ACCOUNT}",)) -> bytes:
    lines = list(preamble) + [";".join(header)] + [";".join(r) for r in rows]
    return "\n".join(lines).encode()


class TestParsing:
    def test_fixture(self):
        st = parse_kaspi_statement(FIXTURE.read_bytes(), FIXTURE.name)
        assert (st.bank, st.account_iban) == ("kaspi", ACCOUNT)
        assert (st.period_from, st.period_to) == (date(2026, 1, 1), date(2026, 1, 31))
        assert st.duplicates_in_file == 1  # повтор документа №101
        assert [(t.op_date, t.direction, t.amount_tiyn) for t in st.transactions] == [
            (date(2026, 1, 5), "in", 15_000_000),
            (date(2026, 1, 10), "in", 2_550_050),
            (date(2026, 1, 10), "in", 2_550_050),
            (date(2026, 1, 15), "out", 1_200_000),
            (date(2026, 1, 31), "out", 100_000),
        ]
        first = st.transactions[0]
        assert (first.counterparty, first.knp, first.reference, first.currency) == ("ТОО «Ромашка»", "710", "101", "KZT")
        assert len(first.counterparty_id) == 12
        assert [r for r, _ in st.skipped_rows] == [13]  # «не дата»; итоговая строка пропущена молча

    def test_identical_rows_without_doc_number_are_kept(self):
        st = parse_kaspi_statement(FIXTURE.read_bytes(), FIXTURE.name)
        qr = [t for t in st.transactions if t.counterparty == "Kaspi QR"]
        assert len(qr) == 2
        assert qr[0].dedup_key != qr[1].dedup_key

    def test_pdf(self):
        pdf = parse_kaspi_statement(PDF_FIXTURE.read_bytes(), PDF_FIXTURE.name)
        csv_ = parse_kaspi_statement(FIXTURE.read_bytes(), FIXTURE.name)
        assert (pdf.account_iban, pdf.period_from, pdf.period_to) == (ACCOUNT, date(2026, 1, 1), date(2026, 1, 31))
        assert pdf.skipped_rows == []  # повтор заголовка на 2-й странице не считается ошибкой
        assert [(t.op_date, t.direction, t.amount_tiyn, t.reference) for t in pdf.transactions] == [
            (t.op_date, t.direction, t.amount_tiyn, t.reference) for t in csv_.transactions
        ]
        # назначение в PDF обрезано, но ключ по референсу совпадает
        assert pdf.transactions[0].purpose != csv_.transactions[0].purpose
        assert [t.dedup_key for t in pdf.transactions] == [t.dedup_key for t in csv_.transactions]

    def test_broken_pdf(self):
        with pytest.raises(StatementParseError, match="PDF"):
            parse_kaspi_statement(b"%PDF-1.7 garbage", "statement.pdf")

    def test_currency_column(self):
        data = rows_csv([["01.01.2026", "1", "", "100", "А", "x", "USD"]], header=HEADER + ["Валюта"])
        assert parse_kaspi_statement(data, "a.csv").transactions[0].currency == "USD"

    def test_period_from_dates_when_no_header(self):
        st = parse_kaspi_statement(rows_csv([["03.01.2026", "1", "", "1", "А", "x"], ["09.01.2026", "2", "", "1", "А", "x"]]), "a.csv")
        assert (st.period_from, st.period_to) == (date(2026, 1, 3), date(2026, 1, 9))

    def test_cp1251_and_comma_delimiter(self):
        data = "\n".join(
            ["Дата,Сумма,Детали", '01.02.2026,"-1 500,25",Покупка', '02.02.26,"+3 000",Перевод']
        ).encode("cp1251")
        st = parse_kaspi_statement(data, "x.csv")
        assert [(t.direction, t.amount_tiyn) for t in st.transactions] == [("out", 150_025), ("in", 300_000)]
        assert st.transactions[1].op_date == date(2026, 2, 2)
        assert st.account_iban is None

    def test_xlsx(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["Выписка", None])
        ws.append([f"Счёт {ACCOUNT}"])
        ws.append(HEADER)
        ws.append([datetime(2026, 3, 1, 12, 0), 7, None, 1250000.5, "ТОО Альфа", "Оплата"])
        ws.append(["02.03.2026", "8", 99.99, None, "ТОО Бета", "Аренда"])
        ws.append([None, None, None, None, None, None])
        buf = io.BytesIO()
        wb.save(buf)
        st = parse_kaspi_statement(buf.getvalue(), "statement.xlsx")
        assert st.account_iban == ACCOUNT
        assert [(t.op_date, t.direction, t.amount_tiyn, t.reference) for t in st.transactions] == [
            (date(2026, 3, 1), "in", 125_000_050, "7"),
            (date(2026, 3, 2), "out", 9_999, "8"),
        ]

    def test_fingerprint_depends_on_account(self):
        rows = [["01.01.2026", "1", "", "100", "А", "x"]]
        a = parse_kaspi_statement(rows_csv(rows), "a.csv").transactions[0]
        b = parse_kaspi_statement(rows_csv(rows, preamble=(f"Счёт: {make_kz_iban('601')}",)), "b.csv").transactions[0]
        assert a.dedup_key != b.dedup_key

    def test_no_header(self):
        with pytest.raises(StatementParseError, match="заголовка"):
            parse_rows([["a", "b"], ["1", "2"]])

    def test_unsupported_format(self):
        with pytest.raises(StatementParseError, match="не поддерживается"):
            parse_kaspi_statement(b"<html>", "statement.html")

    def test_bad_amount_is_skipped_not_fatal(self):
        st = parse_kaspi_statement(rows_csv([["01.01.2026", "1", "", "сто", "А", "x"], ["01.01.2026", "2", "", "1", "А", "x"]]), "a.csv")
        assert len(st.transactions) == 1
        assert st.skipped_rows[0][0] == 3


class TestDeduplication:
    def _import(self, session, data: bytes, name="s.csv", user=None):
        user = user or session.scalar(select(User)) or make_user(session)
        return save_statement(session, parse_kaspi_statement(data, name), user_id=user.id, raw=data, filename=name)

    def test_reimport_same_file(self, db):
        data = FIXTURE.read_bytes()
        first = self._import(db, data)
        assert (first.parsed, first.inserted, first.duplicates) == (5, 5, 1)
        second = self._import(db, data)
        assert (second.inserted, second.duplicates) == (0, 6)

    def test_overlapping_periods(self, db):
        jan = [
            ["05.01.2026", "1", "", "100", "А", "x"],
            ["20.01.2026", "2", "", "200", "Б", "y"],
        ]
        jan_feb = [
            ["20.01.2026", "2", "", "200", "Б", "y"],
            ["03.02.2026", "3", "50", "", "В", "z"],
        ]
        assert self._import(db, rows_csv(jan)).inserted == 2
        r = self._import(db, rows_csv(jan_feb))
        assert (r.inserted, r.duplicates) == (1, 1)

    def test_account_iban_required(self, db):
        data = rows_csv([["05.01.2026", "1", "", "100", "А", "x"]], preamble=())
        with pytest.raises(StatementParseError, match="номер счёта"):
            self._import(db, data)
        parsed = parse_kaspi_statement(data, "a.csv")
        user = db.scalar(select(User))
        assert save_statement(db, parsed, user_id=user.id, raw=data, filename="a.csv", account_iban=ACCOUNT).inserted == 1

    def test_pdf_after_csv_adds_nothing(self, db):
        assert self._import(db, FIXTURE.read_bytes()).inserted == 5
        r = self._import(db, PDF_FIXTURE.read_bytes(), "s.pdf")
        assert (r.parsed, r.inserted) == (5, 0)

    def test_same_reference_other_account_is_not_duplicate(self, db):
        rows = [["01.01.2026", "1", "", "100", "А", "x"]]
        assert self._import(db, rows_csv(rows)).inserted == 1
        other = rows_csv(rows, preamble=(f"Счёт: {make_kz_iban('601')}",))
        assert self._import(db, other).inserted == 1

    def test_pii_encrypted_at_rest(self, db):
        self._import(db, FIXTURE.read_bytes(), "Иванов_январь.csv")
        raw = db.execute(text(
            "SELECT counterparty, counterparty_id, counterparty_account, purpose FROM transactions"
        )).all()
        for row in raw:
            assert all(v.startswith("v1:") for v in row)
        dump = repr(raw) + repr(db.execute(text("SELECT * FROM bank_accounts")).all())
        dump += repr(db.execute(text("SELECT * FROM statements")).all())
        for secret in (ACCOUNT, "Ромашка", "СМАГУЛОВ", "Иванов", "Возврат займа"):
            assert secret not in dump
        # через ORM данные читаются расшифрованными
        account = db.scalar(select(BankAccount))
        assert (account.iban, account.iban_masked) == (ACCOUNT, "KZ18…5678")
        tx = db.scalars(select(Transaction).order_by(Transaction.id)).first()
        assert (tx.counterparty, tx.purpose) == ("ТОО «Ромашка»", "Оплата по счёту № 12 за услуги")
        assert tx.confirmed_by_user is False and tx.category is None

    def test_statement_record(self, db):
        r = self._import(db, FIXTURE.read_bytes())
        st = db.get(Statement, r.statement_id)
        assert (st.period_from, st.period_to, st.status) == (date(2026, 1, 1), date(2026, 1, 31), "imported")
        assert (st.rows_parsed, st.rows_inserted, st.rows_duplicate, st.rows_skipped) == (5, 5, 1, 1)
        assert st.file_ref.startswith("sha256:")

    def test_counterparty_hash_groups_same_counterparty(self, db):
        rows = [["01.01.2026", "1", "", "100", "ТОО А", "x"], ["02.01.2026", "2", "", "100", "тоо а", "y"]]
        self._import(db, rows_csv(rows))
        hashes = db.scalars(select(Transaction.counterparty_hash)).all()
        assert len(set(hashes)) == 1

    def test_accounts_are_per_user(self, db):
        alice = make_user(db)
        bob = make_user(db, iin=make_kz_id("90020240012"), full_name="Другов Друг")
        assert self._import(db, FIXTURE.read_bytes(), user=alice).inserted == 5
        assert self._import(db, FIXTURE.read_bytes(), user=bob).inserted == 5  # чужой счёт — не дубль
        owners = db.scalars(select(BankAccount.user_id).order_by(BankAccount.user_id)).all()
        assert owners == [alice.id, bob.id]

    def test_upload_is_audited(self, db):
        r = self._import(db, FIXTURE.read_bytes(), "Иванов.csv")
        entry = db.scalar(select(AuditLog).where(AuditLog.action == "statement.upload"))
        assert (entry.object_id, entry.details["inserted"]) == (str(r.statement_id), 5)
        assert "Иванов" not in repr(entry.details)
