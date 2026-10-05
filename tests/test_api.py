import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from salyq.db import Base, get_session
from salyq.main import create_app
from tests.test_kaspi_statement import FIXTURE


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    app = create_app(init_db=False)

    def _session():
        with Session(engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_session] = _session
    return TestClient(app)


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_tax_simplified(client):
    r = client.post("/api/v1/tax/simplified", json={"year": 2026, "half": 1, "income_tiyn": 1_000_000_000})
    assert r.status_code == 200
    body = r.json()
    assert body["total_payable_tiyn"] == 40_000_000
    assert body["trace"]["config_version"] == "2026.1"


def test_tax_errors(client):
    assert client.post("/api/v1/tax/simplified", json={"year": 1999, "half": 1, "income_tiyn": 1}).status_code == 404
    r = client.post("/api/v1/tax/simplified", json={"year": 2026, "half": 1, "income_tiyn": 1, "maslikhat_rate": "0.5"})
    assert r.status_code == 422


def test_anonymize_does_not_leak_mapping(client):
    r = client.post("/api/v1/privacy/anonymize", json={"text": "ИП Иванов Иван Иванович, тел. 87011234567"})
    body = r.json()
    assert body["text"] == "ИП [PERSON_1], тел. [PHONE_1]"
    assert "Иванов" not in r.text


def test_upload_statement_twice(client):
    files = {"file": (FIXTURE.name, FIXTURE.read_bytes(), "text/csv")}
    first = client.post("/api/v1/statements/kaspi", files=files).json()
    assert (first["inserted"], first["duplicates"]) == (5, 1)
    second = client.post("/api/v1/statements/kaspi", files=files).json()
    assert second["inserted"] == 0


def test_upload_bad_file(client):
    r = client.post("/api/v1/statements/kaspi", files={"file": ("x.pdf", b"%PDF", "application/pdf")})
    assert r.status_code == 422
