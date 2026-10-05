"""Таблицы по разделу 5 ТЗ (часть, нужная налоговому движку и импорту выписок).

users, consents, tax_calculations, declarations_910 и прочие появятся вместе с
авторизацией и сборкой 910.00.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from salyq.crypto import EncryptedText
from salyq.db import Base

_BigId = BigInteger().with_variant(Integer, "sqlite")


class BankAccount(Base):
    __tablename__ = "bank_accounts"
    __table_args__ = (UniqueConstraint("bank", "iban_hash", name="uq_bank_accounts_iban"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bank: Mapped[str] = mapped_column(String(32))
    iban: Mapped[str] = mapped_column(EncryptedText)  # полный IBAN только в зашифрованном виде
    iban_masked: Mapped[str] = mapped_column(String(16))
    iban_hash: Mapped[str] = mapped_column(String(64))  # слепой индекс для поиска
    currency: Mapped[str] = mapped_column(String(3), default="KZT")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Statement(Base):
    __tablename__ = "statements"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("bank_accounts.id"), index=True)
    file_ref: Mapped[str] = mapped_column(String(255))  # ссылка на файл в хранилище в РК
    file_name: Mapped[str] = mapped_column(EncryptedText)  # имя файла может содержать ФИО
    file_sha256: Mapped[str] = mapped_column(String(64), index=True)
    period_from: Mapped[date | None] = mapped_column(Date)
    period_to: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16))
    rows_parsed: Mapped[int] = mapped_column(Integer)
    rows_inserted: Mapped[int] = mapped_column(Integer)
    rows_duplicate: Mapped[int] = mapped_column(Integer)
    rows_skipped: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("account_id", "fingerprint", name="uq_transactions_fingerprint"),
        CheckConstraint("amount_tiyn > 0", name="ck_transactions_amount_positive"),
        CheckConstraint("direction IN ('in', 'out')", name="ck_transactions_direction"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_transactions_confidence"),
    )

    id: Mapped[int] = mapped_column(_BigId, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("bank_accounts.id"), index=True)
    statement_id: Mapped[int] = mapped_column(ForeignKey("statements.id"))
    op_date: Mapped[date] = mapped_column("date", Date, index=True)
    amount_tiyn: Mapped[int] = mapped_column(BigInteger)  # в минимальных единицах валюты операции
    currency: Mapped[str] = mapped_column(String(3), default="KZT")
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))  # курс НБ РК на дату (ТЗ 4.3)
    direction: Mapped[str] = mapped_column(String(3))
    reference: Mapped[str] = mapped_column(String(64), default="")  # номер документа банка
    counterparty: Mapped[str] = mapped_column(EncryptedText, default="")
    counterparty_id: Mapped[str] = mapped_column(EncryptedText, default="")  # ИИН/БИН
    counterparty_account: Mapped[str] = mapped_column(EncryptedText, default="")
    counterparty_hash: Mapped[str] = mapped_column(String(64), index=True)  # для правил разметки
    knp: Mapped[str] = mapped_column(String(3), default="")
    purpose: Mapped[str] = mapped_column(EncryptedText, default="")  # часто содержит ФИО
    operation: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str | None] = mapped_column(String(32))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    confirmed_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    fingerprint: Mapped[str] = mapped_column(String(64))  # HMAC ключа дедупликации


class RegionRate(Base):
    """Справочник ставок маслихатов (ТЗ 4.1, 4.4)."""

    __tablename__ = "region_rates"
    __table_args__ = (
        UniqueConstraint("region_code", "activity_code", "valid_from", name="uq_region_rates"),
        CheckConstraint("rate > 0 AND rate < 1", name="ck_region_rates_rate"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    region_code: Mapped[str] = mapped_column(String(16), index=True)  # КАТО
    activity_code: Mapped[str] = mapped_column(String(16), default="")  # ОКЭД; "" — для всех видов
    rate: Mapped[Decimal] = mapped_column(Numeric(6, 4))
    valid_from: Mapped[date] = mapped_column(Date)
    source_url: Mapped[str] = mapped_column(Text)
