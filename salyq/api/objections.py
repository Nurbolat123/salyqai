from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from salyq import objections
from salyq.api.deps import DbSession, PdUser
from salyq.models import Objection

router = APIRouter(prefix="/objections", tags=["objections"])


class ObjectionIn(BaseModel):
    subject_ref: str  # transaction:12 | tax_calculation:5 | declaration_910:3
    text: str


@router.post("", status_code=201)
def create(body: ObjectionIn, session: DbSession, user: PdUser) -> dict[str, Any]:
    """Возражение против автоматизированного решения (ст. 19-1): ответ за 3 рабочих дня."""
    try:
        return objections.out(objections.create(session, user, body.subject_ref, body.text))
    except objections.ObjectionError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("")
def my_objections(session: DbSession, user: PdUser) -> list[dict[str, Any]]:
    rows = session.scalars(select(Objection).where(Objection.user_id == user.id).order_by(Objection.id.desc()))
    return [objections.out(o) for o in rows]
