from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from salyq.auth import service
from salyq.auth.clock import utcnow
from salyq.auth.ecp import DevEcpVerifier, build_verifier
from salyq.models import AuditLog, AuthChallenge, AuthSession, User
from salyq.settings import Settings
from tests.helpers import make_kz_id

IIN = make_kz_id("85010130012")
NAME = "Иванов Иван Иванович"
SETTINGS = Settings(ecp_verifier="dev")


def login_as(client: TestClient, iin: str = IIN, full_name: str = NAME) -> dict:
    """Вход через API с dev-подписью; ставит Bearer-токен в клиент."""
    nonce = client.post("/api/v1/auth/ecp/challenge").json()["nonce"]
    r = client.post("/api/v1/auth/ecp", json={"nonce": nonce, "signed_data": DevEcpVerifier.sign(nonce, iin, full_name)})
    assert r.status_code == 200, r.text
    body = r.json()
    client.headers["Authorization"] = f"Bearer {body['token']}"
    return body


def _login(db, iin=IIN, name=NAME, *, sign_nonce=None):
    nonce, _ = service.create_challenge(db, SETTINGS)
    signed = DevEcpVerifier.sign(sign_nonce or nonce, iin, name)
    return service.login(db, DevEcpVerifier(), SETTINGS, nonce=nonce, signed_data=signed)


class TestSettings:
    def test_dev_verifier_forbidden_in_prod(self):
        with pytest.raises(ValueError, match="запрещён"):
            Settings(environment="prod", ecp_verifier="dev")

    def test_ncanode_not_implemented_yet(self):
        with pytest.raises(NotImplementedError):
            build_verifier(Settings(ecp_verifier="ncanode"))


class TestLogin:
    def test_first_login_registers_user(self, db):
        r = _login(db)
        assert r.is_new_user
        assert (r.user.iin, r.user.full_name) == (IIN, NAME)
        assert service.user_by_token(db, r.token).id == r.user.id
        # в БД нет ни токена, ни nonce в открытом виде
        assert db.scalar(select(AuthSession.token_hash)) != r.token
        raw = db.execute(text("SELECT iin, full_name, iin_hash FROM users")).all()
        assert IIN not in repr(raw) and "Иванов" not in repr(raw)

    def test_second_login_finds_same_user_and_updates_name(self, db):
        first = _login(db)
        second = _login(db, name="Иванов Иван Иванович-Новый")
        assert not second.is_new_user and second.user.id == first.user.id
        assert second.user.full_name == "Иванов Иван Иванович-Новый"

    def test_nonce_is_single_use(self, db):
        nonce, _ = service.create_challenge(db, SETTINGS)
        signed = DevEcpVerifier.sign(nonce, IIN, NAME)
        service.login(db, DevEcpVerifier(), SETTINGS, nonce=nonce, signed_data=signed)
        with pytest.raises(service.AuthError):
            service.login(db, DevEcpVerifier(), SETTINGS, nonce=nonce, signed_data=signed)

    def test_expired_nonce(self, db):
        nonce, _ = service.create_challenge(db, SETTINGS)
        db.scalar(select(AuthChallenge)).expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
        with pytest.raises(service.AuthError):
            service.login(db, DevEcpVerifier(), SETTINGS, nonce=nonce, signed_data=DevEcpVerifier.sign(nonce, IIN, NAME))

    @pytest.mark.parametrize(
        ("iin", "sign_nonce"),
        [(IIN, "чужой-nonce"), ("123456789012", None), (make_kz_id("05014000001"), None)],  # чужой nonce, плохой ИИН, БИН
    )
    def test_bad_signature(self, db, iin, sign_nonce):
        with pytest.raises(service.AuthError):
            _login(db, iin=iin, sign_nonce=sign_nonce)
        assert db.scalar(select(User)) is None
        failed = db.scalars(select(AuditLog).where(AuditLog.action == "auth.login_failed")).all()
        assert [f.details["reason"] for f in failed] == ["bad_signature"]

    def test_failed_signature_burns_nonce(self, db):
        nonce, _ = service.create_challenge(db, SETTINGS)
        with pytest.raises(service.AuthError):
            service.login(db, DevEcpVerifier(), SETTINGS, nonce=nonce, signed_data="мусор")
        with pytest.raises(service.AuthError):
            service.login(db, DevEcpVerifier(), SETTINGS, nonce=nonce, signed_data=DevEcpVerifier.sign(nonce, IIN, NAME))

    def test_logout_and_expiry(self, db):
        r = _login(db)
        service.logout(db, r.token)
        assert service.user_by_token(db, r.token) is None
        r2 = _login(db)
        db.scalar(select(AuthSession).where(AuthSession.revoked_at.is_(None))).expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
        assert service.user_by_token(db, r2.token) is None

    def test_audit_has_no_pii(self, db):
        _login(db)
        entries = db.scalars(select(AuditLog)).all()
        assert [e.action for e in entries] == ["auth.login"]
        assert entries[0].details == {"new_user": True}
        assert IIN not in repr([(e.details, e.object_id) for e in entries])


class TestApi:
    def test_login_flow(self, anon):
        body = login_as(anon)
        assert body["is_new_user"] is True
        assert body["missing_consents"] == ["pd_processing"]
        assert body["user"]["iin_masked"] == f"{IIN[:2]}••••••••{IIN[-2:]}"
        assert anon.get("/api/v1/me").json()["full_name"] == NAME
        assert anon.delete("/api/v1/auth/session").status_code == 204
        assert anon.get("/api/v1/me").status_code == 401

    def test_bad_login(self, anon):
        nonce = anon.post("/api/v1/auth/ecp/challenge").json()["nonce"]
        r = anon.post("/api/v1/auth/ecp", json={"nonce": nonce, "signed_data": "AAAA"})
        assert r.status_code == 401

    def test_no_token(self, anon):
        assert anon.get("/api/v1/me").status_code == 401
        assert anon.get("/api/v1/me", headers={"Authorization": "Bearer nope"}).status_code == 401

    def test_profile_update(self, anon):
        login_as(anon)
        r = anon.patch("/api/v1/me", json={"region_code": "750000000", "activity_code": "62010",
                                            "ip_registered_on": "2024-03-01", "employees_count": 2})
        assert r.status_code == 200
        assert (r.json()["region_code"], r.json()["employees_count"]) == ("750000000", 2)
        assert anon.patch("/api/v1/me", json={"region_code": "Алматы"}).status_code == 422
        assert anon.patch("/api/v1/me", json={"employees_count": -1}).status_code == 422
