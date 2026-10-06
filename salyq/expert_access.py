"""Доступ эксперта к данным клиента: только по обращению, на ограниченное время,
каждое открытие — в журнал (ТЗ 4.8, 6)."""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from salyq.auth import audit
from salyq.auth.clock import utcnow
from salyq.models import Declaration910, ExpertAccess, ExpertQuestion, Objection, User

ACCESS_HOURS = 2


class AccessDenied(PermissionError):
    pass


def _valid_reason(session: Session, user_id: int, reason_ref: str) -> bool:
    kind, _, raw = reason_ref.partition(":")
    if not raw.isdigit():
        return False
    if kind == "objection":
        o = session.get(Objection, int(raw))
        return o is not None and o.user_id == user_id and o.resolved_at is None
    if kind == "declaration_910":
        d = session.get(Declaration910, int(raw))
        return d is not None and d.user_id == user_id and d.status == "checked"
    if kind == "question":
        q = session.get(ExpertQuestion, int(raw))
        return q is not None and q.user_id == user_id and q.resolved_at is None
    return False


def grant(session: Session, expert: User, user_id: int, reason_ref: str) -> ExpertAccess:
    if not _valid_reason(session, user_id, reason_ref):
        raise AccessDenied("доступ только по открытому обращению этого клиента")
    now = utcnow()
    access = ExpertAccess(expert_id=expert.id, user_id=user_id, reason_ref=reason_ref,
                          granted_at=now, expires_at=now + timedelta(hours=ACCESS_HOURS))
    session.add(access)
    audit.record(session, "expert.access_grant", actor_type="expert", actor_id=expert.id,
                 object_type="user", object_id=user_id, reason=reason_ref)
    session.commit()
    return access


def require(session: Session, expert: User, user_id: int, what: str) -> None:
    now = utcnow()
    active = session.scalar(select(ExpertAccess).where(
        ExpertAccess.expert_id == expert.id, ExpertAccess.user_id == user_id, ExpertAccess.expires_at > now,
    ))
    if active is None:
        raise AccessDenied("нет действующего доступа к данным клиента — откройте его по обращению")
    audit.record(session, "expert.data_view", actor_type="expert", actor_id=expert.id,
                 object_type="user", object_id=user_id, what=what, reason=active.reason_ref)
    session.commit()
