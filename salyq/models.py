from datetime import date, datetime

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from salyq.db import Base


class BankAccount(Base):
    __tablename__ = "bank_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    iban: Mapped[str] = mapped_column(String(34), unique=True)
    bank: Mapped[str] = mapped_column(String(32), default="kaspi")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StatementImport(Base):
    __tablename__ = "statement_imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("bank_accounts.id"))
    filename: Mapped[str] = mapped_column(String(255))
    file_sha256: Mapped[str] = mapped_column(String(64), index=True)
    rows_parsed: Mapped[int] = mapped_column(Integer)
    rows_inserted: Mapped[int] = mapped_column(Integer)
    rows_duplicate: Mapped[int] = mapped_column(Integer)
    rows_skipped: Mapped[int] = mapped_column(Integer)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BankTransaction(Base):
    __tablename__ = "bank_transactions"
    __table_args__ = (
        UniqueConstraint("account_id", "fingerprint", name="uq_bank_tx_fingerprint"),
        CheckConstraint("amount_tiyn > 0", name="ck_bank_tx_amount_positive"),
        CheckConstraint("direction IN ('in', 'out')", name="ck_bank_tx_direction"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("bank_accounts.id"), index=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("statement_imports.id"))
    op_date: Mapped[date] = mapped_column(Date, index=True)
    amount_tiyn: Mapped[int] = mapped_column(BigInteger)
    direction: Mapped[str] = mapped_column(String(3))
    counterparty: Mapped[str] = mapped_column(Text, default="")
    counterparty_id: Mapped[str] = mapped_column(String(12), default="")
    counterparty_account: Mapped[str] = mapped_column(String(34), default="")
    knp: Mapped[str] = mapped_column(String(3), default="")
    purpose: Mapped[str] = mapped_column(Text, default="")
    doc_number: Mapped[str] = mapped_column(String(64), default="")
    operation: Mapped[str] = mapped_column(Text, default="")
    fingerprint: Mapped[str] = mapped_column(String(64))
