"""Миграции на реальном PostgreSQL: схема из миграций совпадает с моделями,
откат работает, журнал действий нельзя изменить."""

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from salyq.auth import audit
from salyq.db import Base
from tests.conftest import alembic_config

pytestmark = pytest.mark.postgres


def test_schema_matches_models(pg_engine):
    with pg_engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == []


def test_downgrade_and_upgrade_again(pg_engine):
    with pg_engine.begin() as conn:
        cfg = alembic_config(conn)
        command.downgrade(cfg, "base")
        tables = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")).scalars().all()
        assert tables == ["alembic_version"]
        command.upgrade(cfg, "head")


@pytest.mark.parametrize("statement", ["UPDATE audit_log SET action = 'x'", "DELETE FROM audit_log", "TRUNCATE audit_log"])
def test_audit_log_is_append_only(postgres_session, statement):
    audit.record(postgres_session, "test.event", actor_type="system")
    postgres_session.commit()
    with pytest.raises(DBAPIError, match="запрещены"):
        postgres_session.execute(text(statement))
    postgres_session.rollback()
