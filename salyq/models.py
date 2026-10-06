"""Таблицы по разделу 5 ТЗ.

Ещё не реализованы: tax_calculations, declarations_910, objections, chat_messages,
reminders — появятся вместе с соответствующими функциями.
Схема меняется только миграциями Alembic (migrations/).
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from salyq.crypto import EncryptedText
from salyq.db import Base

_BigId = BigInteger().with_variant(Integer, "sqlite")
_Json = JSON().with_variant(JSONB, "postgresql")

CONSENT_TYPES = ("pd_processing", "automated_processing", "cross_border")


class User(Base):
    """ИП. ИИН и ФИО берутся из сертификата ЭЦП и хранятся зашифрованными."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("iin_hash", name="uq_users_iin_hash"),
        CheckConstraint("employees_count >= 0", name="ck_users_employees"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    iin: Mapped[str] = mapped_column(EncryptedText)
    iin_hash: Mapped[str] = mapped_column(String(64))  # слепой индекс для входа
    full_name: Mapped[str] = mapped_column(EncryptedText)
    region_code: Mapped[str | None] = mapped_column(String(16))  # КАТО
    activity_code: Mapped[str | None] = mapped_column(String(16))  # ОКЭД
    ip_registered_on: Mapped[date | None] = mapped_column(Date)
    employees_count: Mapped[int] = mapped_column(Integer, default=0)
    # Заявленный ежемесячный доход для соцплатежей за себя; None — минимальный (1 МЗП)
    declared_income_tiyn: Mapped[int | None] = mapped_column(BigInteger)
    role: Mapped[str] = mapped_column(String(16), default="client", server_default="client")  # client | expert
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Consent(Base):
    """Каждое согласие — отдельная строка; отзыв проставляет revoked_at (история сохраняется)."""

    __tablename__ = "consents"
    __table_args__ = (
        CheckConstraint(f"type IN {CONSENT_TYPES}", name="ck_consents_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    type: Mapped[str] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(32))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthChallenge(Base):
    """Одноразовая строка, которую пользователь подписывает ЭЦП при входе."""

    __tablename__ = "auth_challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    nonce_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(Base):
    """Сессия входа. В БД только хеш токена — утечка таблицы не даёт войти."""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    """Журнал действий (ТЗ 2, 4.8, 6). Только добавление; в PostgreSQL UPDATE/DELETE
    запрещены триггером. В details — никаких ПДн, только идентификаторы и коды."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(_BigId, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    actor_type: Mapped[str] = mapped_column(String(16))  # user | expert | system
    actor_id: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(64), index=True)
    object_type: Mapped[str | None] = mapped_column(String(32))
    object_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(_Json, default=dict)


class BankAccount(Base):
    __tablename__ = "bank_accounts"
    __table_args__ = (UniqueConstraint("user_id", "bank", "iban_hash", name="uq_bank_accounts_iban"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
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
    category_source: Mapped[str | None] = mapped_column(String(64))  # код правила | llm | user
    confirmed_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    region_code: Mapped[str | None] = mapped_column(String(16))  # если доход не в регионе из профиля
    amount_kzt_tiyn: Mapped[int | None] = mapped_column(BigInteger)  # сумма в тенге по курсу НБ РК
    fingerprint: Mapped[str] = mapped_column(String(64))  # HMAC ключа дедупликации


class CategorizationRule(Base):
    """Персональное правило: подтверждения пользователя по контрагенту (ТЗ 4.3)."""

    __tablename__ = "categorization_rules"
    __table_args__ = (
        UniqueConstraint("user_id", "counterparty_hash", "direction", name="uq_categorization_rules"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    counterparty_hash: Mapped[str] = mapped_column(String(64))
    direction: Mapped[str] = mapped_column(String(3))
    category: Mapped[str] = mapped_column(String(32))
    confirmations: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FxRate(Base):
    """Официальный курс НБ РК: сколько тенге за `nominal` единиц валюты на дату."""

    __tablename__ = "fx_rates"
    __table_args__ = (UniqueConstraint("currency", "rate_date", name="uq_fx_rates"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    currency: Mapped[str] = mapped_column(String(3))
    rate_date: Mapped[date] = mapped_column(Date)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 6))  # тенге за 1 единицу валюты
    source: Mapped[str] = mapped_column(String(64), default="nationalbank.kz")


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


class TaxConfigVersion(Base):
    """Версия налоговой конфигурации года в БД. Действует последняя утверждённая
    экспертом (ТЗ 4.4, 4.8); пока таких нет — черновик из salyq/tax/rates/<год>.yaml."""

    __tablename__ = "tax_config_versions"
    __table_args__ = (UniqueConstraint("year", "version", name="uq_tax_config_versions"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    year: Mapped[int] = mapped_column(Integer, index=True)
    version: Mapped[str] = mapped_column(String(32))
    raw_yaml: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))  # draft | approved
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaxCalculation(Base):
    """Сохранённый расчёт со «следом» для экрана «Как посчитано» (ТЗ 4.4, ст. 43 ЦК)."""

    __tablename__ = "tax_calculations"

    id: Mapped[int] = mapped_column(_BigId, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    period: Mapped[str] = mapped_column(String(7))  # 2026H2
    income_tiyn: Mapped[int] = mapped_column(BigInteger)
    tax_tiyn: Mapped[int] = mapped_column(BigInteger)
    config_version: Mapped[str] = mapped_column(String(32))
    config_sha256: Mapped[str] = mapped_column(String(64))
    inputs_hash: Mapped[str] = mapped_column(String(64))  # одинаковые входные данные — тот же расчёт
    trace_json: Mapped[dict] = mapped_column(_Json)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Declaration910(Base):
    """Декларация 910.00: draft → checked → signed → exported (ТЗ 4.5).

    Новый черновик за тот же период переводит прежние неподписанные в superseded.
    """

    __tablename__ = "declarations_910"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'checked', 'signed', 'exported', 'superseded')", name="ck_declarations_910_status"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    period: Mapped[str] = mapped_column(String(7))
    calculation_id: Mapped[int] = mapped_column(ForeignKey("tax_calculations.id"))
    payload_json: Mapped[dict] = mapped_column(_Json)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    checks_json: Mapped[list] = mapped_column(_Json)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expert_reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    expert_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signature: Mapped[str | None] = mapped_column(EncryptedText)  # CMS содержит сертификат с ИИН
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
