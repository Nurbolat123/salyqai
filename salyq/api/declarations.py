from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select

from salyq.api.deps import AppSettings, DbSession, PdUser, get_verifier
from salyq.auth.ecp import EcpVerifier
from salyq.declarations import service
from salyq.models import Declaration910, User
from salyq.tax.summary import PeriodError

router = APIRouter(prefix="/declarations/910", tags=["declarations"])


def decl_out(user: User, d: Declaration910) -> dict[str, Any]:
    return {
        "id": d.id, "period": d.period, "status": d.status, "created_at": d.created_at,
        "checks": d.checks_json, "document": service.document(user, d),
        "digest_to_sign": service.document_digest(user, d) if d.status == "checked" else None,
        "expert_reviewed": d.expert_reviewed_at is not None, "signed_at": d.signed_at, "exported_at": d.exported_at,
    }


def _get(session, user: User, decl_id: int) -> Declaration910:
    d = session.get(Declaration910, decl_id)
    if d is None or d.user_id != user.id:
        raise HTTPException(404, "декларация не найдена")
    return d


class BuildIn(BaseModel):
    period: str


@router.post("", status_code=201)
def build(body: BuildIn, session: DbSession, user: PdUser) -> dict[str, Any]:
    try:
        return decl_out(user, service.build_draft(session, user, body.period))
    except (PeriodError, service.DeclarationError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("")
def list_declarations(session: DbSession, user: PdUser) -> list[dict[str, Any]]:
    rows = session.scalars(select(Declaration910).where(Declaration910.user_id == user.id)
                           .order_by(Declaration910.id.desc()))
    return [{"id": d.id, "period": d.period, "status": d.status, "created_at": d.created_at} for d in rows]


@router.get("/{decl_id}")
def get_declaration(decl_id: int, session: DbSession, user: PdUser) -> dict[str, Any]:
    return decl_out(user, _get(session, user, decl_id))


class SignIn(BaseModel):
    signed_data: str  # CMS-подпись digest_to_sign из NCALayer / eGov Mobile


@router.post("/{decl_id}/sign")
def sign(
    decl_id: int, body: SignIn, session: DbSession, user: PdUser, settings: AppSettings,
    verifier: Annotated[EcpVerifier, Depends(get_verifier)],
) -> dict[str, Any]:
    d = _get(session, user, decl_id)
    try:
        service.sign(session, user, d, signed_data=body.signed_data, verifier=verifier, settings=settings)
    except service.DeclarationError as exc:
        raise HTTPException(409, str(exc)) from exc
    return decl_out(user, d)


@router.get("/{decl_id}/export")
def export(decl_id: int, session: DbSession, user: PdUser) -> Response:
    d = _get(session, user, decl_id)
    try:
        filename, data = service.export_xml(session, user, d)
    except service.DeclarationError as exc:
        raise HTTPException(409, str(exc)) from exc
    return Response(data, media_type="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
