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


@pytest.fixture
def postgres_session():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL не задан")
    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as s:
        yield s
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def db(request):
    """Сессия на SQLite и (если задан TEST_DATABASE_URL) на PostgreSQL."""
    return request.getfixturevalue(f"{request.param}_session")
