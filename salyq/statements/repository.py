"""Сохранение выписки в БД с дедупликацией по (account_id, fingerprint)."""

import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from salyq.crypto import blind_index, mask_iban
from salyq.models import BankAccount, Statement, Transaction
from salyq.statements.kaspi import ParsedStatement, StatementParseError

_BATCH = 1000


@dataclass(frozen=True)
class ImportResult:
    statement_id: int
    account_iban_masked: str
    parsed: int
    inserted: int
    duplicates: int  # уже были в БД + дубли внутри файла
    skipped: list[tuple[int, str]]


def _get_or_create_account(session: Session, bank: str, iban: str) -> BankAccount:
    iban_hash = blind_index(iban, "iban")
    account = session.scalar(
        select(BankAccount).where(BankAccount.bank == bank, BankAccount.iban_hash == iban_hash)
    )
    if account is None:
        account = BankAccount(bank=bank, iban=iban, iban_masked=mask_iban(iban), iban_hash=iban_hash)
        session.add(account)
        session.flush()
    return account


def _counterparty_hash(tx) -> str:
    # ИИН/БИН надёжнее имени; без него — по нормализованному наименованию
    key = tx.counterparty_id or tx.counterparty_account.replace(" ", "").upper() or tx.counterparty.lower()
    return blind_index(key, "counterparty")


def save_statement(
    session: Session, parsed: ParsedStatement, *, raw: bytes, filename: str, account_iban: str | None = None
) -> ImportResult:
    iban = (account_iban or parsed.account_iban or "").replace(" ", "").upper()
    if not iban:
        raise StatementParseError("в выписке не найден номер счёта — передайте account_iban явно")
    if parsed.account_iban and account_iban and parsed.account_iban != iban:
        raise StatementParseError("номер счёта в файле не совпадает с указанным")

    account = _get_or_create_account(session, parsed.bank, iban)
    sha = hashlib.sha256(raw).hexdigest()
    st = Statement(
        account_id=account.id, file_ref=f"sha256:{sha}", file_name=filename, file_sha256=sha,
        period_from=parsed.period_from, period_to=parsed.period_to, status="imported",
        rows_parsed=len(parsed.transactions), rows_inserted=0, rows_duplicate=0,
        rows_skipped=len(parsed.skipped_rows),
    )
    session.add(st)
    session.flush()

    # Шифрование полей делает тип колонки EncryptedText и при Core-вставке
    values = [
        {
            "account_id": account.id, "statement_id": st.id, "date": tx.op_date,
            "amount_tiyn": tx.amount_tiyn, "currency": tx.currency, "direction": tx.direction,
            "reference": tx.reference, "counterparty": tx.counterparty,
            "counterparty_id": tx.counterparty_id, "counterparty_account": tx.counterparty_account,
            "counterparty_hash": _counterparty_hash(tx), "knp": tx.knp, "purpose": tx.purpose,
            "operation": tx.operation, "confirmed_by_user": False,
            "fingerprint": blind_index(tx.dedup_key, "tx"),
        }
        for tx in parsed.transactions
    ]

    inserted = 0
    insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    # Пачками, чтобы не упереться в лимит параметров запроса PostgreSQL (65535)
    for start in range(0, len(values), _BATCH):
        stmt = (
            insert(Transaction.__table__)
            .values(values[start:start + _BATCH])
            .on_conflict_do_nothing(index_elements=["account_id", "fingerprint"])
            .returning(Transaction.__table__.c.id)
        )
        inserted += len(session.execute(stmt).all())

    st.rows_inserted = inserted
    st.rows_duplicate = len(values) - inserted + parsed.duplicates_in_file
    session.commit()
    return ImportResult(
        statement_id=st.id, account_iban_masked=account.iban_masked, parsed=len(values),
        inserted=inserted, duplicates=st.rows_duplicate, skipped=parsed.skipped_rows,
    )
