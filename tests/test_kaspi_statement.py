import io
from datetime import date, datetime
from pathlib import Path

import pytest
from openpyxl import Workbook

from salyq.statements import StatementParseError, parse_kaspi_statement, parse_rows
from salyq.statements.repository import save_statement
from tests.helpers import make_kz_iban

FIXTURE = Path(__file__).parent / "fixtures" / "kaspi_business_2026_01.csv"
ACCOUNT = make_kz_iban()

HEADER = ["Дата операции", "Номер документа", "Дебет", "Кредит", "Контрагент", "Назначение платежа"]


def rows_csv(rows: list[list[str]], header=HEADER, preamble=(f"Счёт: {ACCOUNT}",)) -> bytes:
    lines = list(preamble) + [";".join(header)] + [";".join(r) for r in rows]
    return "\n".join(lines).encode()


class TestParsing:
    def test_fixture(self):
        st = parse_kaspi_statement(FIXTURE.read_bytes(), FIXTURE.name)
        assert st.account_iban == ACCOUNT
        assert st.duplicates_in_file == 1  # повтор документа №101
        assert [(t.op_date, t.direction, t.amount_tiyn) for t in st.transactions] == [
            (date(2026, 1, 5), "in", 15_000_000),
            (date(2026, 1, 10), "in", 2_550_050),
            (date(2026, 1, 10), "in", 2_550_050),
            (date(2026, 1, 15), "out", 1_200_000),
            (date(2026, 1, 31), "out", 100_000),
        ]
        first = st.transactions[0]
        assert (first.counterparty, first.knp, first.doc_number) == ("ТОО «Ромашка»", "710", "101")
        assert len(first.counterparty_id) == 12
        assert [r for r, _ in st.skipped_rows] == [13]  # «не дата»; итоговая строка пропущена молча

    def test_identical_rows_without_doc_number_are_kept(self):
        st = parse_kaspi_statement(FIXTURE.read_bytes(), FIXTURE.name)
        qr = [t for t in st.transactions if t.counterparty == "Kaspi QR"]
        assert len(qr) == 2
        assert qr[0].fingerprint != qr[1].fingerprint

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
        assert [(t.op_date, t.direction, t.amount_tiyn, t.doc_number) for t in st.transactions] == [
            (date(2026, 3, 1), "in", 125_000_050, "7"),
            (date(2026, 3, 2), "out", 9_999, "8"),
        ]

    def test_fingerprint_depends_on_account(self):
        rows = [["01.01.2026", "1", "", "100", "А", "x"]]
        a = parse_kaspi_statement(rows_csv(rows), "a.csv").transactions[0]
        b = parse_kaspi_statement(rows_csv(rows, preamble=(f"Счёт: {make_kz_iban('601')}",)), "b.csv").transactions[0]
        assert a.fingerprint != b.fingerprint

    def test_no_header(self):
        with pytest.raises(StatementParseError, match="заголовка"):
            parse_rows([["a", "b"], ["1", "2"]])

    def test_unsupported_format(self):
        with pytest.raises(StatementParseError, match="не поддерживается"):
            parse_kaspi_statement(b"%PDF-1.7", "statement.pdf")

    def test_bad_amount_is_skipped_not_fatal(self):
        st = parse_kaspi_statement(rows_csv([["01.01.2026", "1", "", "сто", "А", "x"], ["01.01.2026", "2", "", "1", "А", "x"]]), "a.csv")
        assert len(st.transactions) == 1
        assert st.skipped_rows[0][0] == 3


class TestDeduplication:
    def _import(self, session, data: bytes, name="s.csv"):
        return save_statement(session, parse_kaspi_statement(data, name), raw=data, filename=name)

    def test_reimport_same_file(self, sqlite_session):
        data = FIXTURE.read_bytes()
        first = self._import(sqlite_session, data)
        assert (first.parsed, first.inserted, first.duplicates) == (5, 5, 1)
        second = self._import(sqlite_session, data)
        assert (second.inserted, second.duplicates) == (0, 6)

    def test_overlapping_periods(self, sqlite_session):
        jan = [
            ["05.01.2026", "1", "", "100", "А", "x"],
            ["20.01.2026", "2", "", "200", "Б", "y"],
        ]
        jan_feb = [
            ["20.01.2026", "2", "", "200", "Б", "y"],
            ["03.02.2026", "3", "50", "", "В", "z"],
        ]
        assert self._import(sqlite_session, rows_csv(jan)).inserted == 2
        r = self._import(sqlite_session, rows_csv(jan_feb))
        assert (r.inserted, r.duplicates) == (1, 1)

    def test_account_iban_required(self, sqlite_session):
        data = rows_csv([["05.01.2026", "1", "", "100", "А", "x"]], preamble=())
        with pytest.raises(StatementParseError, match="номер счёта"):
            self._import(sqlite_session, data)
        parsed = parse_kaspi_statement(data, "a.csv")
        assert save_statement(sqlite_session, parsed, raw=data, filename="a.csv", account_iban=ACCOUNT).inserted == 1

    @pytest.mark.postgres
    def test_postgres_reimport(self, pg_session):
        data = FIXTURE.read_bytes()
        assert self._import(pg_session, data).inserted == 5
        assert self._import(pg_session, data).inserted == 0
