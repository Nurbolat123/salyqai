"""Возражения против автоматизированных решений (ст. 19-1 Закона № 94-V, ТЗ 2, 4.8)."""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from salyq.auth import audit
from salyq.auth.clock import aware, utcnow
from salyq.categorize.service import get_user_transaction
from salyq.models import Declaration910, Objection, TaxCalculation, User
from salyq.workdays import workday_deadline

RESPONSE_WORKDAYS = 3
SUBJECT_KINDS = ("transaction", "tax_calculation", "declaration_910")


class ObjectionError(ValueError):
    pass


def _owns(session: Session, user: User, subject_ref: str) -> bool:
    kind, _, raw_id = subject_ref.partition(":")
    if kind not in SUBJECT_KINDS or not raw_id.isdigit():
        raise ObjectionError(f"subject_ref в формате <{'|'.join(SUBJECT_KINDS)}>:<id>")
    obj_id = int(raw_id)
    if kind == "transaction":
        return get_user_transaction(session, user, obj_id) is not None
    model = TaxCalculation if kind == "tax_calculation" else Declaration910
    obj = session.get(model, obj_id)
    return obj is not None and obj.user_id == user.id


def create(session: Session, user: User, subject_ref: str, text: str) -> Objection:
    text = text.strip()
    if not 3 <= len(text) <= 5000:
        raise ObjectionError("опишите возражение (от 3 до 5000 символов)")
    if not _owns(session, user, subject_ref):
        raise ObjectionError("объект возражения не найден")
    now = utcnow()
    obj = Objection(user_id=user.id, subject_ref=subject_ref, text=text, created_at=now,
                    due_at=workday_deadline(now, RESPONSE_WORKDAYS))
    session.add(obj)
    session.flush()
    audit.record(session, "objection.create", actor_type="user", actor_id=user.id,
                 object_type="objection", object_id=obj.id, subject=subject_ref)
    session.commit()
    return obj


def resolve(session: Session, expert: User, obj: Objection, resolution: str) -> Objection:
    if obj.resolved_at is not None:
        raise ObjectionError("возражение уже рассмотрено")
    if not resolution.strip():
        raise ObjectionError("нужен ответ клиенту")
    obj.resolved_at, obj.resolved_by, obj.resolution = utcnow(), expert.id, resolution.strip()
    audit.record(session, "objection.resolve", actor_type="expert", actor_id=expert.id,
                 object_type="objection", object_id=obj.id, overdue=aware(obj.resolved_at) > aware(obj.due_at))
    session.commit()
    return obj


def out(obj: Objection, now: datetime | None = None, with_text: bool = True) -> dict[str, Any]:
    now = now or utcnow()
    due = aware(obj.due_at)
    return {
        "id": obj.id, "subject_ref": obj.subject_ref, "created_at": obj.created_at, "due_at": due,
        "status": "resolved" if obj.resolved_at else "open",
        "overdue": obj.resolved_at is None and now > due,
        "seconds_left": None if obj.resolved_at else int((due - now).total_seconds()),
        **({"text": obj.text, "resolution": obj.resolution} if with_text else {}),
        "resolved_at": obj.resolved_at,
    }


def open_queue(session: Session) -> list[Objection]:
    return list(session.scalars(select(Objection).where(Objection.resolved_at.is_(None)).order_by(Objection.due_at)))
