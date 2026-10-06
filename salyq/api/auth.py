from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from salyq.api.deps import AppSettings, DbSession, bearer_token, get_verifier
from salyq.api.me import profile
from salyq.auth import consents
from salyq.auth.ecp import EcpVerifier
from salyq.auth.service import AuthError, create_challenge, login, logout

router = APIRouter(prefix="/auth", tags=["auth"])


class ChallengeOut(BaseModel):
    nonce: str
    expires_at: datetime


class EcpLoginIn(BaseModel):
    nonce: str
    signed_data: str  # CMS-подпись nonce из NCALayer / eGov Mobile (base64)


@router.post("/ecp/challenge")
def challenge(session: DbSession, settings: AppSettings) -> ChallengeOut:
    nonce, expires_at = create_challenge(session, settings)
    return ChallengeOut(nonce=nonce, expires_at=expires_at)


@router.post("/ecp")
def ecp_login(
    body: EcpLoginIn, session: DbSession, settings: AppSettings,
    verifier: Annotated[EcpVerifier, Depends(get_verifier)],
) -> dict[str, Any]:
    try:
        r = login(session, verifier, settings, nonce=body.nonce, signed_data=body.signed_data)
    except AuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    return {
        "token": r.token, "expires_at": r.expires_at, "is_new_user": r.is_new_user,
        "user": profile(r.user), "missing_consents": consents.missing_required(session, r.user),
    }


@router.delete("/session", status_code=204)
def end_session(session: DbSession, token: Annotated[str, Depends(bearer_token)]) -> Response:
    logout(session, token)
    return Response(status_code=204)
