"""Журнал действий. Запись добавляется в текущую транзакцию: если действие
откатилось, записи о нём тоже не будет."""

from typing import Any, Literal

from sqlalchemy.orm import Session

from salyq.auth.clock import utcnow
from salyq.models import AuditLog

ActorType = Literal["user", "expert", "system", "anonymous"]


def record(
    session: Session,
    action: str,
    *,
    actor_type: ActorType,
    actor_id: int | None = None,
    object_type: str | None = None,
    object_id: str | int | None = None,
    **details: Any,
) -> None:
    """details — только коды и идентификаторы, без персональных данных."""
    session.add(AuditLog(
        at=utcnow(), actor_type=actor_type, actor_id=actor_id, action=action,
        object_type=object_type, object_id=None if object_id is None else str(object_id),
        details=details,
    ))
