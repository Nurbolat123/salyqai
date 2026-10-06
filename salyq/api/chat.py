from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from salyq.api.deps import AppSettings, DbSession, PdUser
from salyq.chat import service
from salyq.models import ChatMessage, ExpertQuestion

router = APIRouter(prefix="/chat", tags=["chat"])


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.post("/messages")
def send(body: MessageIn, session: DbSession, user: PdUser, settings: AppSettings) -> dict[str, Any]:
    a = service.answer(session, user, body.text, service.choose_provider(session, user, settings))
    return {"answer": a.text, "provider": a.provider, "cards": a.cards, "sources": a.sources}


@router.get("/messages")
def history(session: DbSession, user: PdUser) -> list[dict[str, Any]]:
    rows = session.scalars(select(ChatMessage).where(ChatMessage.user_id == user.id).order_by(ChatMessage.id))
    return [{"id": m.id, "role": m.role, "content": m.content, "created_at": m.created_at, "meta": m.meta} for m in rows]


class EscalateIn(BaseModel):
    text: str | None = Field(None, max_length=4000)


@router.post("/escalate", status_code=201)
def escalate(body: EscalateIn, session: DbSession, user: PdUser) -> dict[str, Any]:
    """«Передать эксперту»."""
    try:
        q = service.escalate(session, user, body.text)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"question_id": q.id, "created_at": q.created_at}


@router.get("/questions")
def my_questions(session: DbSession, user: PdUser) -> list[dict[str, Any]]:
    rows = session.scalars(select(ExpertQuestion).where(ExpertQuestion.user_id == user.id)
                           .order_by(ExpertQuestion.id.desc()))
    return [{"id": q.id, "text": q.text, "answer": q.answer, "created_at": q.created_at,
             "resolved_at": q.resolved_at} for q in rows]
