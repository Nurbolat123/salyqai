import base64
import os

# Тестовые ключи шифрования; задаются до первого обращения к настройкам
os.environ.setdefault("SALYQ_FIELD_KEY", base64.b64encode(b"f" * 32).decode())
os.environ.setdefault("SALYQ_HMAC_KEY", base64.b64encode(b"h" * 32).decode())

import pytest  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from salyq import models  # noqa: F401,E402
from salyq.db import Base  # noqa: E402


@pytest.fixture
def sqlite_session():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as s:
        yield s


def alembic_config(connection):
    from pathlib import Path

    from alembic.config import Config

    root = Path(__file__).parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.attributes["connection"] = connection
    return cfg


@pytest.fixture
def pg_engine():
    """PostgreSQL со схемой из миграций (а не create_all) — так миграции проверяются каждым прогоном."""
    from alembic import command

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL не задан")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        command.upgrade(alembic_config(conn), "head")
    yield engine
    engine.dispose()


@pytest.fixture
def postgres_session(pg_engine):
    with Session(pg_engine, expire_on_commit=False) as s:
        yield s


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def db(request):
    """Сессия на SQLite и (если задан TEST_DATABASE_URL) на PostgreSQL."""
    return request.getfixturevalue(f"{request.param}_session")


@pytest.fixture
def engine():
    """SQLite в памяти, общий для всех соединений TestClient."""
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def anon(engine):
    """API-клиент без входа."""
    from fastapi.testclient import TestClient

    from salyq.db import get_session
    from salyq.main import create_app

    app = create_app()

    def _session():
        with Session(engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_session] = _session
    return TestClient(app)


@pytest.fixture
def client(anon):
    """Клиент, вошедший по ЭЦП и давший согласие на обработку ПДн."""
    from salyq.auth.consents import CONSENT_VERSIONS
    from tests.test_auth import login_as

    login_as(anon)
    r = anon.post("/api/v1/consents", json={"type": "pd_processing", "version": CONSENT_VERSIONS["pd_processing"]})
    assert r.status_code == 201
    return anon
