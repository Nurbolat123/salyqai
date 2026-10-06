from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from salyq.models import RegionRate
from tests.test_auth import login_as
from tests.test_kaspi_statement import FIXTURE, PDF_FIXTURE


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def _calc(client, regions, year=2026):
    return client.post("/api/v1/tax/simplified", json={"year": year, "half": 1, "regions": regions})


def test_tax_simplified_uses_region_rates(client, engine):
    with Session(engine) as s:
        s.add(RegionRate(region_code="750000000", rate=Decimal("0.03"), valid_from=date(2026, 1, 1), source_url="u"))
        s.commit()
    r = _calc(client, [{"region_code": "750000000", "income_tiyn": 1_000_000_000},
                       {"region_code": "710000000", "income_tiyn": 1_000_000_000}])
    assert r.status_code == 200
    body = r.json()
    assert [x["rate"] for x in body["regions"]] == ["0.0300", "0.04"]
    assert body["total_payable_tiyn"] == 70_000_000
    assert body["deadlines"] == {"declaration_910": "2026-08-15", "tax_payment": "2026-08-25"}
    assert {w["code"] for w in body["warnings"]} == {"REGION_RATE_MISSING", "CONFIG_NOT_APPROVED"}
    assert body["trace"]["config_version"] == "2026.1"


def test_tax_errors(client):
    assert _calc(client, [{"region_code": "1", "income_tiyn": 1}], year=1999).status_code == 404
    assert _calc(client, []).status_code == 422
    assert _calc(client, [{"region_code": "1", "income_tiyn": -1}]).status_code == 422


def test_anonymize_does_not_leak_mapping(client):
    r = client.post("/api/v1/privacy/anonymize", json={"text": "ИП Иванов Иван Иванович, тел. 87011234567, 5 000 ₸"})
    body = r.json()
    assert body["text"] == "ИП [PERSON_1], тел. [PHONE_1], [СУММА В ТЕНГЕ: до 10 тыс.]"
    assert "Иванов" not in r.text


def test_upload_statement_twice(client):
    files = {"file": (FIXTURE.name, FIXTURE.read_bytes(), "text/csv")}
    first = client.post("/api/v1/statements", files=files, data={"bank": "kaspi"}).json()
    assert (first["inserted"], first["duplicates"], first["account"]) == (5, 1, "KZ18…5678")
    assert (first["period_from"], first["period_to"]) == ("2026-01-01", "2026-01-31")
    pdf = {"file": (PDF_FIXTURE.name, PDF_FIXTURE.read_bytes(), "application/pdf")}
    assert client.post("/api/v1/statements", files=pdf).json()["inserted"] == 0


def test_upload_errors(client):
    bad = client.post("/api/v1/statements", files={"file": ("x.pdf", b"%PDF-1.7 x", "application/pdf")})
    assert bad.status_code == 422
    other_bank = client.post("/api/v1/statements", files={"file": ("x.csv", b"", "text/csv")}, data={"bank": "halyk"})
    assert other_bank.status_code == 422 and "halyk" in other_bank.json()["detail"]


def test_statements_and_privacy_need_login_and_consent(anon):
    files = {"file": (FIXTURE.name, FIXTURE.read_bytes(), "text/csv")}
    assert anon.post("/api/v1/statements", files=files).status_code == 401
    assert anon.post("/api/v1/privacy/anonymize", json={"text": "x"}).status_code == 401
    login_as(anon)
    r = anon.post("/api/v1/statements", files=files)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "CONSENT_REQUIRED"


def test_metrics_and_cors(client):
    client.get("/health")
    body = client.get("/metrics").text
    assert 'salyq_http_requests_total{method="GET",route="/health",status="200"}' in body
    r = client.options("/api/v1/me", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
