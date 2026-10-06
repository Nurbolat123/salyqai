import xml.etree.ElementTree as ET
from datetime import date

import pytest
from sqlalchemy import select

from salyq.auth import consents
from salyq.auth.ecp import DevEcpVerifier
from salyq.categorize import service as cat
from salyq.declarations import service
from salyq.models import AuditLog, Declaration910
from salyq.settings import Settings
from salyq.statements import parse_kaspi_statement
from salyq.statements.repository import save_statement
from salyq.tax import config_store
from salyq.tax.config import RATES_DIR
from tests.helpers import make_kz_id, make_user
from tests.test_kaspi_statement import ACCOUNT, HEADER

V = dict(consents.CONSENT_VERSIONS)
TODAY = date(2026, 10, 6)
SETTINGS = Settings(ecp_verifier="dev", declaration_expert_review=True)
IIN = make_kz_id("85010130012")


def statement(rows, period="01.01.2026 - 30.06.2026"):
    lines = [f"Счёт: {ACCOUNT}", f"Период: {period}", ";".join(HEADER)] + [";".join(r) for r in rows]
    return "\n".join(lines).encode()


@pytest.fixture
def expert(db):
    e = make_user(db, iin=make_kz_id("90020240012"), full_name="Эксперт Бухгалтер")
    e.role = "expert"
    db.commit()
    return e


@pytest.fixture
def user(db, expert):
    u = make_user(db, iin=IIN, full_name="Иванов Иван Иванович")
    u.region_code = "750000000"
    db.commit()
    for t in ("pd_processing", "automated_processing"):
        consents.grant(db, u, t, V[t])
    row = config_store.submit(db, expert, (RATES_DIR / "2026.yaml").read_text(encoding="utf-8").replace(
        'version: "2026.1"', 'version: "2026.2"'))
    config_store.approve(db, expert, row.id)
    return u


def load(db, user, rows, period="01.01.2026 - 30.06.2026", confirm=True):
    data = statement(rows, period)
    save_statement(db, parse_kaspi_statement(data, "s.csv"), user_id=user.id, raw=data, filename="s.csv")
    cat.categorize(db, user)
    if confirm:
        cat.confirm_suggested(db, user)


ROWS = [
    ["10.02.2026", "1", "", "1 000 000", "ТОО А", "Оплата по договору"],
    ["10.03.2026", "2", "", "234 567,89", "ТОО Б", "Оплата по договору"],
    ["11.03.2026", "3", "5 000", "", "Kaspi", "Комиссия"],
]


def checks(decl):
    return {c["code"]: c["ok"] for c in decl.checks_json}


class TestBuild:
    def test_ready_declaration(self, db, user):
        load(db, user, ROWS)
        d = service.build_draft(db, user, "2026H1", today=TODAY)
        assert d.status == "checked", d.checks_json
        p = d.payload_json
        assert p["income"]["total_tiyn"] == 123_456_789
        assert p["taxes"]["total_payable_tiyn"] == 4_938_300  # 49 382,72 → 49 383 ₸
        assert [m["month"] for m in p["social_self"]] == [f"2026-0{i}" for i in range(1, 7)]
        # ПДн в payload не хранятся
        assert IIN not in repr(p) and "Иванов" not in repr(p)
        assert service.document(user, d)["header"]["iin"] == IIN

    def test_blocking_checks(self, db, user):
        load(db, user, ROWS + [["12.03.2026", "4", "", "50 000", "Х", "Перевод"]], period="01.01.2026 - 31.03.2026",
             confirm=True)
        d = service.build_draft(db, user, "2026H1", today=date(2026, 5, 1))
        c = checks(d)
        assert d.status == "draft"
        assert not c["period_finished"] and not c["no_unconfirmed_transactions"] and not c["statements_cover_period"]
        assert c["config_approved"] and c["limit_not_exceeded"]
        cover = next(x for x in d.checks_json if x["code"] == "statements_cover_period")
        assert "01.04.2026" in cover["message"]

    def test_unapproved_config_blocks(self, db):
        u = make_user(db, iin=IIN)
        u.region_code = "750000000"
        db.commit()
        d = service.build_draft(db, u, "2026H1", today=TODAY)
        assert not checks(d)["config_approved"]
        assert not checks(d)["statements_cover_period"]

    def test_rebuild_supersedes(self, db, user):
        load(db, user, ROWS)
        d1 = service.build_draft(db, user, "2026H1", today=TODAY)
        d2 = service.build_draft(db, user, "2026H1", today=TODAY)
        db.refresh(d1)
        assert (d1.status, d2.status) == ("superseded", "checked")


class TestSignAndExport:
    def _ready(self, db, user, expert):
        load(db, user, ROWS)
        d = service.build_draft(db, user, "2026H1", today=TODAY)
        service.expert_review(db, expert, d)
        return d

    def test_full_flow(self, db, user, expert):
        d = self._ready(db, user, expert)
        sig = DevEcpVerifier.sign(service.document_digest(user, d), IIN, "Иванов Иван Иванович")
        service.sign(db, user, d, signed_data=sig, verifier=DevEcpVerifier(), settings=SETTINGS)
        assert d.status == "signed"
        name, data = service.export_xml(db, user, d)
        assert name == "910_2026H1.xml" and d.status == "exported"
        root = ET.fromstring(data)
        assert root.find("header").get("iin") == IIN
        assert {f.get("code"): f.text for f in root.findall("field")}["tax_total"] == "49383"
        assert len(root.find("socialSelf")) == 6
        actions = [a.action for a in db.scalars(select(AuditLog).where(AuditLog.object_type == "declaration_910"))]
        assert actions == ["declaration.build", "declaration.expert_review", "declaration.sign", "declaration.export"]
        with pytest.raises(service.DeclarationError, match="уже подписана"):
            service.build_draft(db, user, "2026H1", today=TODAY)

    def test_needs_expert(self, db, user):
        load(db, user, ROWS)
        d = service.build_draft(db, user, "2026H1", today=TODAY)
        sig = DevEcpVerifier.sign(service.document_digest(user, d), IIN, "И")
        with pytest.raises(service.DeclarationError, match="эксперт"):
            service.sign(db, user, d, signed_data=sig, verifier=DevEcpVerifier(), settings=SETTINGS)
        service.sign(db, user, d, signed_data=sig, verifier=DevEcpVerifier(),
                     settings=Settings(ecp_verifier="dev", declaration_expert_review=False))
        assert d.status == "signed"

    @pytest.mark.parametrize("who", ["other_person", "wrong_digest"])
    def test_bad_signature(self, db, user, expert, who):
        d = self._ready(db, user, expert)
        digest = service.document_digest(user, d)
        sig = (DevEcpVerifier.sign(digest, make_kz_id("90020240012"), "Чужой") if who == "other_person"
               else DevEcpVerifier.sign("0" * 64, IIN, "И"))
        with pytest.raises(service.DeclarationError):
            service.sign(db, user, d, signed_data=sig, verifier=DevEcpVerifier(), settings=SETTINGS)
        assert d.status == "checked"

    def test_stale_after_data_change(self, db, user, expert):
        d = self._ready(db, user, expert)
        load(db, user, [["20.06.2026", "9", "", "10 000", "ТОО В", "Оплата по договору"]])
        sig = DevEcpVerifier.sign(service.document_digest(user, d), IIN, "И")
        with pytest.raises(service.DeclarationError, match="изменились"):
            service.sign(db, user, d, signed_data=sig, verifier=DevEcpVerifier(), settings=SETTINGS)

    def test_export_requires_signature(self, db, user, expert):
        d = self._ready(db, user, expert)
        with pytest.raises(service.DeclarationError):
            service.export_xml(db, user, d)


class TestApi:
    def test_build_and_get(self, client):
        r = client.post("/api/v1/declarations/910", json={"period": "2026H1"})
        assert r.status_code == 201
        body = r.json()
        assert body["status"] == "draft" and body["digest_to_sign"] is None
        assert client.get(f"/api/v1/declarations/910/{body['id']}").json()["id"] == body["id"]
        assert len(client.get("/api/v1/declarations/910").json()) == 1
        assert client.post(f"/api/v1/declarations/910/{body['id']}/sign", json={"signed_data": "x"}).status_code == 409
        assert client.get(f"/api/v1/declarations/910/{body['id']}/export").status_code == 409
        assert client.get("/api/v1/declarations/910/9999").status_code == 404
        assert client.post("/api/v1/declarations/910", json={"period": "2026"}).status_code == 422
