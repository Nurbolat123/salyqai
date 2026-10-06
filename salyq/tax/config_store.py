"""Версии налоговой конфигурации в БД: черновик → утверждение экспертом (ТЗ 4.4, 4.8)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from salyq.auth import audit
from salyq.auth.clock import utcnow
from salyq.models import TaxConfigVersion, User
from salyq.tax.config import ConfigNotFound, LoadedConfig, load_year, parse_config


class ConfigStoreError(ValueError):
    pass


def _loaded(row: TaxConfigVersion, approver: User | None) -> LoadedConfig:
    loaded = parse_config(row.raw_yaml.encode(), f"db:{row.year}/{row.version}", row.year)
    approved_by = approver.full_name if approver else None
    return loaded.model_copy(update={"config": loaded.config.model_copy(update={"approved_by": approved_by})})


def active_config(session: Session, year: int) -> LoadedConfig:
    """Последняя утверждённая версия года; иначе черновик из репозитория."""
    row = session.scalar(
        select(TaxConfigVersion)
        .where(TaxConfigVersion.year == year, TaxConfigVersion.status == "approved")
        .order_by(TaxConfigVersion.approved_at.desc(), TaxConfigVersion.id.desc())
    )
    if row is not None:
        return _loaded(row, session.get(User, row.approved_by))
    try:
        loaded = load_year(year)
    except ConfigNotFound:
        raise
    # В файле репозитория утверждение не признаём: утверждает только эксперт через БД
    return loaded.model_copy(update={"config": loaded.config.model_copy(update={"approved_by": None})})


def submit(session: Session, expert: User, raw_yaml: str) -> TaxConfigVersion:
    try:
        loaded = parse_config(raw_yaml.encode(), "upload")
    except ValueError as exc:
        raise ConfigStoreError(str(exc)) from exc
    cfg = loaded.config
    exists = session.scalar(select(TaxConfigVersion).where(
        TaxConfigVersion.year == cfg.year, TaxConfigVersion.version == cfg.version))
    if exists:
        raise ConfigStoreError(f"версия {cfg.version} за {cfg.year} уже есть — увеличьте version")
    row = TaxConfigVersion(year=cfg.year, version=cfg.version, raw_yaml=raw_yaml, sha256=loaded.sha256,
                           status="draft", created_by=expert.id, created_at=utcnow())
    session.add(row)
    session.flush()
    audit.record(session, "tax_config.submit", actor_type="expert", actor_id=expert.id,
                 object_type="tax_config", object_id=row.id, year=cfg.year, version=cfg.version)
    session.commit()
    return row


def approve(session: Session, expert: User, version_id: int) -> TaxConfigVersion:
    row = session.get(TaxConfigVersion, version_id)
    if row is None:
        raise ConfigStoreError("версия не найдена")
    if row.status == "approved":
        return row
    row.status, row.approved_by, row.approved_at = "approved", expert.id, utcnow()
    audit.record(session, "tax_config.approve", actor_type="expert", actor_id=expert.id,
                 object_type="tax_config", object_id=row.id, year=row.year, version=row.version,
                 sha256=row.sha256)
    session.commit()
    return row
