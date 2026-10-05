"""Парсер выписки по счёту Kaspi Business (XLSX или CSV-выгрузка).

Kaspi периодически меняет вид выгрузки, поэтому колонки ищутся по набору
синонимов, а строка заголовка — автоматически. Поддерживаются оба варианта
сумм: раздельные «Дебет/Кредит» и одна колонка «Сумма» со знаком.
"""

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Literal

from salyq.money import MoneyError, to_tiyn

Direction = Literal["in", "out"]


class StatementParseError(ValueError):
    pass


COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("дата операции", "дата", "дата проводки", "дата и время", "күні", "date"),
    "doc_number": ("номер документа", "№ документа", "номер док", "№ док", "құжат нөмірі"),
    "debit": ("дебет", "списание", "расход", "сумма списания", "дебет (kzt)", "дебет, kzt"),
    "credit": ("кредит", "поступление", "приход", "сумма поступления", "кредит (kzt)", "кредит, kzt"),
    "amount": ("сумма", "сумма операции", "сумма (kzt)", "сумма, kzt", "сома"),
    "counterparty": (
        "контрагент", "наименование контрагента", "получатель/отправитель",
        "отправитель/получатель", "наименование получателя/отправителя",
        "наименование бенефициара/отправителя", "корреспондент",
    ),
    "counterparty_id": ("иин/бин", "иин/бин контрагента", "бин/иин", "бин/иин контрагента", "жсн/бсн"),
    "counterparty_account": ("счет контрагента", "счёт контрагента", "iban контрагента", "иик контрагента", "иик"),
    "knp": ("кнп", "код назначения платежа"),
    "purpose": ("назначение платежа", "назначение", "детали", "описание", "детали операции", "төлем мақсаты"),
    "operation": ("операция", "тип операции", "вид операции"),
}

_REQUIRED_ANY_AMOUNT = (("debit", "credit"), ("amount",))
_IBAN_RE = re.compile(r"(?<![A-Z0-9])KZ\d{2}(?:\s?[A-Z0-9]){16}(?![A-Z0-9])")
_DATE_RE = re.compile(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})")
_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


@dataclass(frozen=True)
class ParsedTransaction:
    op_date: date
    amount_tiyn: int  # всегда > 0, направление — в direction
    direction: Direction
    counterparty: str
    counterparty_id: str
    counterparty_account: str
    knp: str
    purpose: str
    doc_number: str
    operation: str
    row_number: int  # номер строки в исходном файле (1-based)
    fingerprint: str = ""


@dataclass
class ParsedStatement:
    account_iban: str | None
    transactions: list[ParsedTransaction]
    skipped_rows: list[tuple[int, str]] = field(default_factory=list)  # (строка, причина)
    duplicates_in_file: int = 0


def _norm_header(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower().replace("ё", "е")


def _norm_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r"\s+", " ", str(value)).strip()


def _match_columns(header: list[Any]) -> dict[str, int] | None:
    normalized = [_norm_header(h) for h in header]
    mapping: dict[str, int] = {}
    for field_name, aliases in COLUMN_ALIASES.items():
        wanted = {a.replace("ё", "е") for a in aliases}
        for idx, h in enumerate(normalized):
            if h in wanted and idx not in mapping.values():
                mapping[field_name] = idx
                break
    if "date" not in mapping:
        return None
    if not any(all(f in mapping for f in group) for group in _REQUIRED_ANY_AMOUNT):
        return None
    return mapping


def _parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = _norm_text(value)
    if m := _DATE_RE.search(s):
        d, mth, y = (int(x) for x in m.groups())
        if y < 100:
            y += 2000
        return date(y, mth, d)
    if m := _ISO_DATE_RE.search(s):
        y, mth, d = (int(x) for x in m.groups())
        return date(y, mth, d)
    raise StatementParseError(f"не удалось разобрать дату: {value!r}")


def _parse_amount(value: Any) -> int:
    """Сумма ячейки → тиыны со знаком. Пустая ячейка → 0."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return 0
    if isinstance(value, bool):
        raise StatementParseError(f"некорректная сумма: {value!r}")
    if isinstance(value, int):
        return value * 100
    if isinstance(value, float):
        # Excel хранит числа как float; до 2 знаков repr точен для сумм выписки
        value = repr(round(value, 2))
    s = str(value).strip()
    negative = s.startswith("-") or s.startswith("−") or (s.startswith("(") and s.endswith(")"))
    s = s.strip("()").lstrip("-−+").strip()
    # «1.234.567,89» → «1234567,89»
    if "," in s and "." in s:
        s = s.replace(".", "") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    try:
        tiyn = to_tiyn(s)
    except MoneyError as exc:
        raise StatementParseError(str(exc)) from exc
    return -tiyn if negative else tiyn


def _cell(row: list[Any], mapping: dict[str, int], name: str) -> Any:
    idx = mapping.get(name)
    return row[idx] if idx is not None and idx < len(row) else None


def fingerprint(account: str | None, tx: ParsedTransaction, occurrence: int) -> str:
    """Отпечаток операции для дедупликации.

    occurrence — порядковый номер среди полностью одинаковых строк (дата, сумма,
    контрагент, назначение…) в этом файле. Благодаря ему две реально разные, но
    идентичные операции (два одинаковых платежа в один день) не схлопываются,
    а повторная загрузка того же файла или пересекающегося периода — схлопывается.
    """
    parts = [
        (account or "").upper(),
        tx.op_date.isoformat(),
        tx.direction,
        str(tx.amount_tiyn),
        tx.doc_number.lower(),
        tx.counterparty_id,
        tx.counterparty_account.upper().replace(" ", ""),
        tx.counterparty.lower(),
        tx.knp,
        tx.purpose.lower(),
        str(occurrence),
    ]
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def parse_rows(rows: Iterable[list[Any]]) -> ParsedStatement:
    rows = list(rows)
    account: str | None = None
    mapping: dict[str, int] | None = None
    header_idx = -1
    for i, row in enumerate(rows):
        if account is None:
            for cell in row:
                if m := _IBAN_RE.search(_norm_text(cell).upper()):
                    account = m.group().replace(" ", "")
                    break
        mapping = _match_columns(row)
        if mapping:
            header_idx = i
            break
    if mapping is None:
        raise StatementParseError("не найдена строка заголовка с датой и суммами — это не выписка Kaspi Business?")

    result = ParsedStatement(account_iban=account, transactions=[])
    occurrences: dict[tuple, int] = {}
    seen_doc_numbers: set[tuple[str, str, int]] = set()

    for i, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        if not any(_norm_text(c) for c in row):
            continue
        raw_date = _cell(row, mapping, "date")
        first = _norm_header(row[0] if row else "")
        if first.startswith(("итого", "всего", "остаток", "обороты", "барлығы")):
            continue
        try:
            op_date = _parse_date(raw_date)
        except (StatementParseError, ValueError) as exc:
            result.skipped_rows.append((i, str(exc)))
            continue
        try:
            if "amount" in mapping and not ("debit" in mapping and "credit" in mapping):
                signed = _parse_amount(_cell(row, mapping, "amount"))
            else:
                signed = _parse_amount(_cell(row, mapping, "credit")) - abs(
                    _parse_amount(_cell(row, mapping, "debit"))
                )
        except StatementParseError as exc:
            result.skipped_rows.append((i, str(exc)))
            continue
        if signed == 0:
            result.skipped_rows.append((i, "нулевая сумма"))
            continue

        tx = ParsedTransaction(
            op_date=op_date,
            amount_tiyn=abs(signed),
            direction="in" if signed > 0 else "out",
            counterparty=_norm_text(_cell(row, mapping, "counterparty")),
            counterparty_id=re.sub(r"\D", "", _norm_text(_cell(row, mapping, "counterparty_id"))),
            counterparty_account=_norm_text(_cell(row, mapping, "counterparty_account")),
            knp=_norm_text(_cell(row, mapping, "knp")),
            purpose=_norm_text(_cell(row, mapping, "purpose")),
            doc_number=_norm_text(_cell(row, mapping, "doc_number")),
            operation=_norm_text(_cell(row, mapping, "operation")),
            row_number=i,
        )

        # Явный дубль внутри файла: тот же номер документа, дата и сумма
        if tx.doc_number:
            doc_key = (tx.doc_number, tx.op_date.isoformat(), signed)
            if doc_key in seen_doc_numbers:
                result.duplicates_in_file += 1
                continue
            seen_doc_numbers.add(doc_key)

        key = (tx.op_date, tx.direction, tx.amount_tiyn, tx.doc_number, tx.counterparty_id,
               tx.counterparty_account, tx.counterparty.lower(), tx.knp, tx.purpose.lower())
        occ = occurrences.get(key, 0)
        occurrences[key] = occ + 1
        result.transactions.append(
            ParsedTransaction(**{**tx.__dict__, "fingerprint": fingerprint(account, tx, occ)})
        )
    return result


def _read_csv(data: bytes) -> list[list[str]]:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise StatementParseError("неизвестная кодировка CSV")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";"
    return list(csv.reader(io.StringIO(text), delimiter=delimiter))


def _read_xlsx(data: bytes) -> list[list[Any]]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        return [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def parse_kaspi_statement(data: bytes, filename: str = "") -> ParsedStatement:
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm")) or data[:2] == b"PK":
        rows = _read_xlsx(data)
    elif name.endswith((".csv", ".txt")) or not name:
        rows = _read_csv(data)
    else:
        raise StatementParseError(
            f"формат {filename!r} не поддерживается: загрузите XLSX или CSV (PDF — в следующей итерации)"
        )
    return parse_rows(rows)
