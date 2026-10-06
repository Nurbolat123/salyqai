from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from salyq.api.deps import CurrentUser, DbSession
from salyq.auth import consents

router = APIRouter(prefix="/consents", tags=["consents"])


class ConsentIn(BaseModel):
    type: str
    version: str


@router.get("")
def list_consents(user: CurrentUser, session: DbSession) -> dict[str, Any]:
    active = consents.active_consents(session, user)
    return {
        "current_versions": consents.CONSENT_VERSIONS,
        "required": list(consents.REQUIRED_CONSENTS),
        "active": {t: {"version": c.version, "granted_at": c.granted_at} for t, c in active.items()},
    }


@router.post("", status_code=201)
def give_consent(body: ConsentIn, user: CurrentUser, session: DbSession) -> dict[str, Any]:
    try:
        c = consents.grant(session, user, body.type, body.version)
    except consents.ConsentError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"type": c.type, "version": c.version, "granted_at": c.granted_at}


@router.delete("/{consent_type}", status_code=204)
def revoke_consent(consent_type: str, user: CurrentUser, session: DbSession) -> Response:
    if not consents.revoke(session, user, consent_type):
        raise HTTPException(404, "действующего согласия этого типа нет")
    return Response(status_code=204)
