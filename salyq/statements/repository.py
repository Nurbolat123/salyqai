"""Сохранение выписки в БД с дедупликацией по (account_id, fingerprint)."""

import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from salyq.models import BankAccount, BankTransaction, StatementImport
from salyq.statements.kaspi import ParsedStatement, StatementParseError

_BATCH = 1000


@dataclass(frozen=True)
class ImportResult:
    import_id: int
    account_iban: str
    parsed: int
    inserted: int
    duplicates: int  # уже были в БД + дубли внутри файла
    skipped: list[tuple[int, str]]


def _get_or_create_account(session: Session, iban: str) -> BankAccount:
    account = session.scalar(select(BankAccount).where(BankAccount.iban == iban))
    if account is None:
        account = BankAccount(iban=iban)
        session.add(account)
        session.flush()
    return account


def save_statement(
    session: Session, parsed: ParsedStatement, *, raw: bytes, filename: str, account_iban: str | None = None
) -> ImportResult:
    iban = (account_iban or parsed.account_iban or "").replace(" ", "").upper()
    if not iban:
        raise StatementParseError("в выписке не найден номер счёта — передайте account_iban явно")
    if parsed.account_iban and account_iban and parsed.account_iban != iban:
        raise StatementParseError("номер счёта в файле не совпадает с указанным")

    account = _get_or_create_account(session, iban)
    imp = StatementImport(
        account_id=account.id, filename=filename, file_sha256=hashlib.sha256(raw).hexdigest(),
        rows_parsed=len(parsed.transactions), rows_inserted=0, rows_duplicate=0,
        rows_skipped=len(parsed.skipped_rows),
    )
    session.add(imp)
    session.flush()

    values = [
        {
            "account_id": account.id, "import_id": imp.id, "op_date": tx.op_date,
            "amount_tiyn": tx.amount_tiyn, "direction": tx.direction,
            "counterparty": tx.counterparty, "counterparty_id": tx.counterparty_id,
            "counterparty_account": tx.counterparty_account, "knp": tx.knp,
            "purpose": tx.purpose, "doc_number": tx.doc_number, "operation": tx.operation,
            "fingerprint": tx.fingerprint,
        }
        for tx in parsed.transactions
    ]
    inserted = 0
    insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    # Пачками, чтобы не упереться в лимит параметров запроса PostgreSQL (65535)
    for start in range(0, len(values), _BATCH):
        stmt = (
            insert(BankTransaction)
            .values(values[start:start + _BATCH])
            .on_conflict_do_nothing(index_elements=["account_id", "fingerprint"])
            .returning(BankTransaction.id)
        )
        inserted += len(session.execute(stmt).all())

    imp.rows_inserted = inserted
    imp.rows_duplicate = len(values) - inserted + parsed.duplicates_in_file
    session.commit()
    return ImportResult(
        import_id=imp.id, account_iban=iban, parsed=len(values), inserted=inserted,
        duplicates=imp.rows_duplicate, skipped=parsed.skipped_rows,
    )
