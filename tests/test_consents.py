import pytest
from sqlalchemy import select

from salyq.auth import consents
from salyq.models import AuditLog, Consent
from tests.helpers import make_user
from tests.test_auth import login_as

V = dict(consents.CONSENT_VERSIONS)  # копия: тесты подменяют версии


class TestService:
    def test_grant_and_revoke(self, db):
        user = make_user(db)
        assert consents.missing_required(db, user) == ["pd_processing"]
        consents.grant(db, user, "pd_processing", V["pd_processing"])
        assert consents.has_consent(db, user, "pd_processing")
        assert consents.missing_required(db, user) == []
        assert consents.revoke(db, user, "pd_processing")
        assert not consents.has_consent(db, user, "pd_processing")
        assert not consents.revoke(db, user, "pd_processing")
        # история сохраняется
        assert db.scalars(select(Consent)).one().revoked_at is not None
        assert [e.action for e in db.scalars(select(AuditLog).order_by(AuditLog.id))] == ["consent.grant", "consent.revoke"]

    def test_grant_is_idempotent(self, db):
        user = make_user(db)
        a = consents.grant(db, user, "cross_border", V["cross_border"])
        b = consents.grant(db, user, "cross_border", V["cross_border"])
        assert a.id == b.id

    def test_wrong_version_or_type(self, db):
        user = make_user(db)
        with pytest.raises(consents.ConsentError, match="устаревшая"):
            consents.grant(db, user, "pd_processing", "2020-01-01")
        with pytest.raises(consents.ConsentError, match="неизвестный"):
            consents.grant(db, user, "marketing", "1")

    def test_new_text_version_requires_new_consent(self, db, monkeypatch):
        user = make_user(db)
        consents.grant(db, user, "pd_processing", V["pd_processing"])
        monkeypatch.setitem(consents.CONSENT_VERSIONS, "pd_processing", "2027-01-01")
        assert not consents.has_consent(db, user, "pd_processing")
        consents.grant(db, user, "pd_processing", "2027-01-01")
        rows = db.scalars(select(Consent).order_by(Consent.id)).all()
        assert [(r.version, r.revoked_at is None) for r in rows] == [(V["pd_processing"], False), ("2027-01-01", True)]


class TestApi:
    def test_flow(self, anon):
        login_as(anon)
        info = anon.get("/api/v1/consents").json()
        assert info["required"] == ["pd_processing"] and info["active"] == {}
        r = anon.post("/api/v1/consents", json={"type": "cross_border", "version": info["current_versions"]["cross_border"]})
        assert r.status_code == 201
        assert set(anon.get("/api/v1/consents").json()["active"]) == {"cross_border"}
        assert anon.delete("/api/v1/consents/cross_border").status_code == 204
        assert anon.delete("/api/v1/consents/cross_border").status_code == 404
        assert anon.post("/api/v1/consents", json={"type": "pd_processing", "version": "old"}).status_code == 422

    def test_revoking_pd_consent_blocks_processing(self, client):
        assert client.delete("/api/v1/consents/pd_processing").status_code == 204
        r = client.post("/api/v1/privacy/anonymize", json={"text": "x"})
        assert r.status_code == 403
