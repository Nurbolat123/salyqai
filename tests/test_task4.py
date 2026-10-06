"""Возражения (ст. 19-1), доступ эксперта по обращению, напоминания, админ-API."""

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from salyq import expert_access, objections
from salyq.auth import consents
from salyq.auth.clock import utcnow
from salyq.cli import make_expert
from salyq.models import AuditLog, ExpertQuestion, Reminder
from salyq.reminders.notifiers import MemoryNotifier
from salyq.reminders.service import message, run, upcoming
from salyq.tax.config import RATES_DIR
from salyq.tax.summary import parse_period, tax_summary
from salyq.workdays import ALMATY, add_workdays, is_workday, workday_deadline
from tests.helpers import make_kz_id, make_user
from tests.test_auth import login_as

V = dict(consents.CONSENT_VERSIONS)
EXPERT_IIN = make_kz_id("90020240012")


class TestWorkdays:
    def test_weekends_and_holidays(self):
        assert not is_workday(date(2026, 10, 24))  # суббота
        assert not is_workday(date(2026, 10, 26))  # перенос Дня Республики
        assert is_workday(date(2026, 10, 27))

    def test_add(self):
        assert add_workdays(date(2026, 10, 6), 3) == date(2026, 10, 9)  # вт → пт
        assert add_workdays(date(2026, 10, 22), 3) == date(2026, 10, 28)  # чт → (пт, вт, ср)
        # праздников 2027 в календаре нет — считаются только выходные (срок раньше, т.е. строже)
        assert add_workdays(date(2026, 12, 31), 3) == date(2027, 1, 5)

    def test_deadline_is_end_of_day_in_kz(self):
        created = datetime(2026, 10, 6, 20, 0, tzinfo=ALMATY) - timedelta(hours=5)
        assert workday_deadline(created, 3) == datetime(2026, 10, 9, 23, 59, 59, tzinfo=ALMATY)


@pytest.fixture
def client_user(db):
    u = make_user(db)
    consents.grant(db, u, "pd_processing", V["pd_processing"])
    return u


@pytest.fixture
def expert(db):
    e = make_user(db, iin=EXPERT_IIN, full_name="Эксперт")
    e.role = "expert"
    db.commit()
    return e


def calc_ref(db, user) -> str:
    return f"tax_calculation:{tax_summary(db, user, parse_period('2026H1'))['calculation_id']}"


class TestObjections:
    def test_create_and_resolve(self, db, client_user, expert):
        o = objections.create(db, client_user, calc_ref(db, client_user), "Не согласен с суммой дохода")
        info = objections.out(o)
        assert info["status"] == "open" and not info["overdue"] and info["seconds_left"] > 0
        objections.resolve(db, expert, o, "Проверили: перевод от сестры исключён из дохода")
        assert objections.out(o)["status"] == "resolved"
        with pytest.raises(objections.ObjectionError):
            objections.resolve(db, expert, o, "ещё раз")
        entries = db.scalars(select(AuditLog).where(AuditLog.object_type == "objection")).all()
        assert [e.action for e in entries] == ["objection.create", "objection.resolve"]
        assert "Не согласен" not in repr([e.details for e in entries])

    def test_overdue(self, db, client_user):
        o = objections.create(db, client_user, calc_ref(db, client_user), "текст")
        assert objections.out(o, now=utcnow() + timedelta(days=10))["overdue"]

    @pytest.mark.parametrize("ref", ["tax_calculation:999", "transaction:1", "foo:1", "tax_calculation:x"])
    def test_foreign_or_bad_subject(self, db, client_user, ref):
        with pytest.raises(objections.ObjectionError):
            objections.create(db, client_user, ref, "текст")

    def test_cannot_object_to_someone_elses(self, db, client_user, expert):
        ref = calc_ref(db, client_user)
        with pytest.raises(objections.ObjectionError, match="не найден"):
            objections.create(db, expert, ref, "чужое")


class TestExpertAccess:
    def test_access_only_by_open_request(self, db, client_user, expert):
        with pytest.raises(expert_access.AccessDenied):
            expert_access.require(db, expert, client_user.id, "transactions")
        with pytest.raises(expert_access.AccessDenied):
            expert_access.grant(db, expert, client_user.id, "objection:999")
        o = objections.create(db, client_user, calc_ref(db, client_user), "текст")
        expert_access.grant(db, expert, client_user.id, f"objection:{o.id}")
        expert_access.require(db, expert, client_user.id, "transactions")
        views = db.scalars(select(AuditLog).where(AuditLog.action == "expert.data_view")).all()
        assert views[0].details == {"what": "transactions", "reason": f"objection:{o.id}"}

    def test_access_expires(self, db, client_user, expert):
        o = objections.create(db, client_user, calc_ref(db, client_user), "текст")
        a = expert_access.grant(db, expert, client_user.id, f"objection:{o.id}")
        a.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
        with pytest.raises(expert_access.AccessDenied):
            expert_access.require(db, expert, client_user.id, "x")

    def test_question_reason(self, db, client_user, expert):
        q = ExpertQuestion(user_id=client_user.id, text="Как учитывать доход из-за рубежа?", created_at=utcnow())
        db.add(q)
        db.commit()
        expert_access.grant(db, expert, client_user.id, f"question:{q.id}")


class TestReminders:
    def test_upcoming(self, db):
        kinds = {(k, d) for k, d, _ in upcoming(db, date(2026, 8, 10))}
        assert ("declaration_910", date(2026, 8, 15)) in kinds and ("tax_payment", date(2026, 8, 17)) not in kinds
        assert ("social_payments", date(2026, 8, 25)) not in kinds  # дальше 7 дней
        assert ("social_payments", date(2026, 8, 25)) in {(k, d) for k, d, _ in upcoming(db, date(2026, 8, 18))}

    def test_schedule_and_send_once(self, db, client_user):
        client_user.email = "ip@example.kz"
        db.commit()
        n = MemoryNotifier("email")
        assert run(db, date(2026, 8, 8), [n]) == {"scheduled": 1, "sent": 1}  # за 7 дней до 15.08
        assert run(db, date(2026, 8, 8), [n]) == {"scheduled": 0, "sent": 0}
        address, subject, text = n.sent[0]
        assert address == "ip@example.kz" and "910.00" in subject and "15.08.2026" in text
        assert run(db, date(2026, 8, 12), [n])["sent"] == 1  # за 3 дня

    def test_no_channel_or_consent_no_reminder(self, db, client_user):
        stranger = make_user(db, iin=EXPERT_IIN)
        stranger.email = "x@example.kz"  # без согласия на обработку ПДн
        db.commit()
        assert run(db, date(2026, 8, 8), [MemoryNotifier("email")]) == {"scheduled": 0, "sent": 0}

    def test_failed_send_is_retried(self, db, client_user):
        client_user.email = "ip@example.kz"
        db.commit()

        class Broken(MemoryNotifier):
            def send(self, *a):
                raise RuntimeError("smtp down")

        assert run(db, date(2026, 8, 8), [Broken("email")])["sent"] == 0
        assert db.scalar(select(Reminder)).error == "smtp down"
        assert run(db, date(2026, 8, 9), [MemoryNotifier("email")])["sent"] == 1

    def test_message_has_no_pii(self):
        subject, text = message("tax_payment", date(2026, 8, 25), date(2026, 8, 25))
        assert "сегодня последний день" in text


class TestCli:
    def test_make_expert(self, db, client_user):
        assert "не найден" in make_expert(db, EXPERT_IIN)
        assert "назначен экспертом" in make_expert(db, make_kz_id("85010130012"))
        assert client_user.role == "expert"


class TestApi:
    def _expert_client(self, anon, engine):
        from sqlalchemy.orm import Session

        body = login_as(anon, iin=EXPERT_IIN, full_name="Эксперт")
        with Session(engine) as s:
            from salyq.models import User

            s.get(User, body["user"]["id"]).role = "expert"
            s.commit()
        return anon

    def test_objection_flow(self, client, engine):
        from fastapi.testclient import TestClient

        calc = client.get("/api/v1/tax/summary", params={"period": "2026H1"}).json()["calculation_id"]
        r = client.post("/api/v1/objections", json={"subject_ref": f"tax_calculation:{calc}", "text": "Не согласен"})
        assert r.status_code == 201
        obj_id, owner_id = r.json()["id"], client.get("/api/v1/me").json()["id"]
        assert client.post("/api/v1/objections", json={"subject_ref": "x", "text": "abc"}).status_code == 422
        assert client.get("/api/v1/admin/queue").status_code == 403  # клиент не эксперт

        expert = self._expert_client(TestClient(client.app), engine)
        q = expert.get("/api/v1/admin/queue").json()
        assert q["objections"][0]["id"] == obj_id and "text" not in q["objections"][0]
        assert expert.get(f"/api/v1/admin/objections/{obj_id}").status_code == 403  # доступ не открыт
        assert expert.post("/api/v1/admin/access", json={"user_id": owner_id, "reason_ref": f"objection:{obj_id}"}).status_code == 201
        assert expert.get(f"/api/v1/admin/objections/{obj_id}").json()["text"] == "Не согласен"
        assert expert.get(f"/api/v1/admin/clients/{owner_id}/transactions").status_code == 200
        r = expert.post(f"/api/v1/admin/objections/{obj_id}/resolve", json={"resolution": "Пересчитали"})
        assert r.json()["status"] == "resolved"
        assert client.get("/api/v1/objections").json()[0]["resolution"] == "Пересчитали"

    def test_config_and_rates(self, anon, engine):
        expert = self._expert_client(anon, engine)
        raw = (RATES_DIR / "2026.yaml").read_text(encoding="utf-8").replace('version: "2026.1"', 'version: "2026.9"')
        v = expert.post("/api/v1/admin/tax-config", json={"yaml": raw}).json()
        assert expert.post(f"/api/v1/admin/tax-config/{v['id']}/approve").json()["status"] == "approved"
        assert expert.get("/api/v1/admin/tax-config/2026").json()["active"]["version"] == "2026.9"
        assert expert.post("/api/v1/admin/tax-config", json={"yaml": "x: 1"}).status_code == 422
        r = expert.post("/api/v1/admin/region-rates", json={
            "region_code": "750000000", "rate": "0.03", "valid_from": "2026-01-01", "source_url": "https://maslihat.kz/x"})
        assert r.status_code == 201

    def test_notifications(self, client):
        r = client.patch("/api/v1/me/notifications", json={"email": "a@b.kz", "telegram_chat_id": "123"})
        assert r.json() == {"email": True, "telegram": True, "push": False}
        assert client.patch("/api/v1/me/notifications", json={"email": ""}).json()["email"] is False
        assert client.patch("/api/v1/me/notifications", json={"email": "bad"}).status_code == 422
