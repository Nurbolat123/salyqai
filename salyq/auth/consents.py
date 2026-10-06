"""Согласия (ТЗ 2, 4.1): обработка ПДн, автоматизированная обработка (ст. 19-1),
трансграничная передача. Версии текстов согласий — CONSENT_VERSIONS; при выпуске
новой редакции текста версия меняется, и пользователь должен согласиться заново."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from salyq.auth import audit
from salyq.auth.clock import utcnow
from salyq.models import CONSENT_TYPES, Consent, User

CONSENT_VERSIONS: dict[str, str] = {
    "pd_processing": "2026-10-01",
    "automated_processing": "2026-10-01",
    "cross_border": "2026-10-01",
}
REQUIRED_CONSENTS = ("pd_processing",)  # без него сервис не обрабатывает данные

assert set(CONSENT_VERSIONS) == set(CONSENT_TYPES)


class ConsentError(ValueError):
    pass


def active_consents(session: Session, user: User) -> dict[str, Consent]:
    rows = session.scalars(
        select(Consent).where(Consent.user_id == user.id, Consent.revoked_at.is_(None))
    ).all()
    return {c.type: c for c in rows}


def has_consent(session: Session, user: User, consent_type: str) -> bool:
    """Действует только согласие на текущую редакцию текста."""
    c = active_consents(session, user).get(consent_type)
    return c is not None and c.version == CONSENT_VERSIONS[consent_type]


def missing_required(session: Session, user: User) -> list[str]:
    return [t for t in REQUIRED_CONSENTS if not has_consent(session, user, t)]


def grant(session: Session, user: User, consent_type: str, version: str) -> Consent:
    if consent_type not in CONSENT_VERSIONS:
        raise ConsentError(f"неизвестный тип согласия: {consent_type}")
    if version != CONSENT_VERSIONS[consent_type]:
        raise ConsentError(f"устаревшая редакция согласия: актуальная {CONSENT_VERSIONS[consent_type]}")
    current = active_consents(session, user).get(consent_type)
    if current and current.version == version:
        return current
    now = utcnow()
    if current:  # согласие на прежнюю редакцию заменяется
        current.revoked_at = now
    consent = Consent(user_id=user.id, type=consent_type, version=version, granted_at=now)
    session.add(consent)
    audit.record(session, "consent.grant", actor_type="user", actor_id=user.id,
                 object_type="consent", object_id=consent_type, version=version)
    session.commit()
    return consent


def revoke(session: Session, user: User, consent_type: str) -> bool:
    current = active_consents(session, user).get(consent_type)
    if current is None:
        return False
    current.revoked_at = utcnow()
    audit.record(session, "consent.revoke", actor_type="user", actor_id=user.id,
                 object_type="consent", object_id=consent_type, version=current.version)
    session.commit()
    return True
