"""Админ-панель эксперта (ТЗ 4.8). Данные клиентов — только через доступ по обращению."""

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from salyq import expert_access, objections
from salyq.api.deps import DbSession, ExpertUser
from salyq.api.transactions import tx_out
from salyq.auth import audit
from salyq.auth.clock import utcnow
from salyq.categorize.service import user_transactions
from salyq.declarations import service as declarations
from salyq.models import (
    BankAccount,
    Declaration910,
    ExpertQuestion,
    Objection,
    RegionRate,
    TaxConfigVersion,
    Transaction,
    User,
)
from salyq.tax import config_store
from salyq.tax.config import ConfigNotFound
from salyq.tax.summary import parse_period, tax_summary

router = APIRouter(prefix="/admin", tags=["admin"])


# --- очередь возражений и вопросов -------------------------------------------------

@router.get("/queue")
def queue(session: DbSession, expert: ExpertUser) -> dict[str, Any]:
    """Открытые возражения (с таймером 3 рабочих дня), вопросы и декларации на проверку.
    Тексты обращений не показываются, пока не открыт доступ к клиенту."""
    open_q = session.scalars(select(ExpertQuestion).where(ExpertQuestion.resolved_at.is_(None))
                             .order_by(ExpertQuestion.created_at)).all()
    decls = session.scalars(select(Declaration910).where(
        Declaration910.status == "checked", Declaration910.expert_reviewed_at.is_(None))).all()
    return {
        "objections": [{**objections.out(o, with_text=False), "user_id": o.user_id}
                       for o in objections.open_queue(session)],
        "questions": [{"id": q.id, "user_id": q.user_id, "created_at": q.created_at} for q in open_q],
        "declarations": [{"id": d.id, "user_id": d.user_id, "period": d.period, "created_at": d.created_at}
                         for d in decls],
    }


class AccessIn(BaseModel):
    user_id: int
    reason_ref: str  # objection:7 | question:3 | declaration_910:2


@router.post("/access", status_code=201)
def open_access(body: AccessIn, session: DbSession, expert: ExpertUser) -> dict[str, Any]:
    try:
        a = expert_access.grant(session, expert, body.user_id, body.reason_ref)
    except expert_access.AccessDenied as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"user_id": a.user_id, "reason_ref": a.reason_ref, "expires_at": a.expires_at}


def _client(session, expert: User, user_id: int, what: str) -> User:
    try:
        expert_access.require(session, expert, user_id, what)
    except expert_access.AccessDenied as exc:
        raise HTTPException(403, str(exc)) from exc
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "клиент не найден")
    return user


@router.get("/clients/{user_id}/transactions")
def client_transactions(user_id: int, session: DbSession, expert: ExpertUser) -> list[dict[str, Any]]:
    user = _client(session, expert, user_id, "transactions")
    rows = session.execute(user_transactions(user).add_columns(BankAccount)
                           .order_by(Transaction.op_date.desc())).all()
    return [tx_out(tx, acc) for tx, acc in rows]


@router.get("/clients/{user_id}/summary")
def client_summary(user_id: int, period: str, session: DbSession, expert: ExpertUser) -> dict[str, Any]:
    user = _client(session, expert, user_id, "tax_summary")
    return tax_summary(session, user, parse_period(period))


@router.get("/objections/{obj_id}")
def get_objection(obj_id: int, session: DbSession, expert: ExpertUser) -> dict[str, Any]:
    o = session.get(Objection, obj_id)
    if o is None:
        raise HTTPException(404, "не найдено")
    _client(session, expert, o.user_id, f"objection:{o.id}")
    return {**objections.out(o), "user_id": o.user_id}


class ResolveIn(BaseModel):
    resolution: str = Field(min_length=1, max_length=5000)


@router.post("/objections/{obj_id}/resolve")
def resolve_objection(obj_id: int, body: ResolveIn, session: DbSession, expert: ExpertUser) -> dict[str, Any]:
    o = session.get(Objection, obj_id)
    if o is None:
        raise HTTPException(404, "не найдено")
    _client(session, expert, o.user_id, f"objection:{o.id}")
    try:
        return objections.out(objections.resolve(session, expert, o, body.resolution))
    except objections.ObjectionError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/questions/{q_id}")
def get_question(q_id: int, session: DbSession, expert: ExpertUser) -> dict[str, Any]:
    q = session.get(ExpertQuestion, q_id)
    if q is None:
        raise HTTPException(404, "не найдено")
    _client(session, expert, q.user_id, f"question:{q.id}")
    return {"id": q.id, "user_id": q.user_id, "text": q.text, "created_at": q.created_at,
            "answer": q.answer, "resolved_at": q.resolved_at}


class AnswerIn(BaseModel):
    answer: str = Field(min_length=1, max_length=10000)


@router.post("/questions/{q_id}/answer")
def answer_question(q_id: int, body: AnswerIn, session: DbSession, expert: ExpertUser) -> dict[str, Any]:
    q = session.get(ExpertQuestion, q_id)
    if q is None:
        raise HTTPException(404, "не найдено")
    _client(session, expert, q.user_id, f"question:{q.id}")
    if q.resolved_at is not None:
        raise HTTPException(409, "уже отвечено")
    q.answer, q.resolved_at, q.resolved_by = body.answer, utcnow(), expert.id
    audit.record(session, "question.answer", actor_type="expert", actor_id=expert.id,
                 object_type="question", object_id=q.id)
    session.commit()
    return {"id": q.id, "resolved_at": q.resolved_at}


@router.get("/declarations/{decl_id}")
def get_declaration(decl_id: int, session: DbSession, expert: ExpertUser) -> dict[str, Any]:
    d = session.get(Declaration910, decl_id)
    if d is None:
        raise HTTPException(404, "не найдено")
    user = _client(session, expert, d.user_id, f"declaration_910:{d.id}")
    return {"id": d.id, "status": d.status, "checks": d.checks_json, "document": declarations.document(user, d)}


@router.post("/declarations/{decl_id}/review")
def review_declaration(decl_id: int, session: DbSession, expert: ExpertUser) -> dict[str, Any]:
    d = session.get(Declaration910, decl_id)
    if d is None:
        raise HTTPException(404, "не найдено")
    _client(session, expert, d.user_id, f"declaration_910:{d.id}")
    try:
        declarations.expert_review(session, expert, d)
    except declarations.DeclarationError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"id": d.id, "expert_reviewed_at": d.expert_reviewed_at}


# --- конфигурация ставок и сроков ----------------------------------------------------

@router.get("/tax-config/{year}")
def get_tax_config(year: int, session: DbSession, expert: ExpertUser) -> dict[str, Any]:
    try:
        active = config_store.active_config(session, year)
    except ConfigNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    versions = session.scalars(select(TaxConfigVersion).where(TaxConfigVersion.year == year)
                               .order_by(TaxConfigVersion.id)).all()
    return {
        "active": {"version": active.config.version, "sha256": active.sha256, "approved_by": active.config.approved_by,
                   "source": active.path},
        "versions": [{"id": v.id, "version": v.version, "status": v.status, "created_at": v.created_at,
                      "approved_at": v.approved_at, "sha256": v.sha256} for v in versions],
    }


class ConfigIn(BaseModel):
    yaml: str


@router.post("/tax-config", status_code=201)
def submit_tax_config(body: ConfigIn, session: DbSession, expert: ExpertUser) -> dict[str, Any]:
    try:
        v = config_store.submit(session, expert, body.yaml)
    except config_store.ConfigStoreError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"id": v.id, "year": v.year, "version": v.version, "status": v.status}


@router.post("/tax-config/{version_id}/approve")
def approve_tax_config(version_id: int, session: DbSession, expert: ExpertUser) -> dict[str, Any]:
    try:
        v = config_store.approve(session, expert, version_id)
    except config_store.ConfigStoreError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"id": v.id, "status": v.status, "approved_at": v.approved_at}


class RegionRateIn(BaseModel):
    region_code: str = Field(pattern=r"^\d{9}$")
    activity_code: str = Field("", pattern=r"^(\d{5})?$")
    rate: Decimal = Field(gt=0, lt=1)
    valid_from: date
    source_url: str = Field(min_length=5)


@router.post("/region-rates", status_code=201)
def add_region_rate(body: RegionRateIn, session: DbSession, expert: ExpertUser) -> dict[str, Any]:
    r = RegionRate(**body.model_dump())
    session.add(r)
    session.flush()
    audit.record(session, "region_rate.add", actor_type="expert", actor_id=expert.id, object_type="region_rate",
                 object_id=r.id, region=r.region_code, rate=str(r.rate), valid_from=r.valid_from.isoformat())
    session.commit()
    return {"id": r.id}
