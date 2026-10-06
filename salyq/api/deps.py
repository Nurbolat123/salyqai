from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from salyq.auth import consents
from salyq.auth.ecp import EcpVerifier, build_verifier
from salyq.auth.service import user_by_token
from salyq.db import get_session
from salyq.models import User
from salyq.settings import Settings, get_settings

_bearer = HTTPBearer(auto_error=False)

DbSession = Annotated[Session, Depends(get_session)]
AppSettings = Annotated[Settings, Depends(get_settings)]


def get_verifier(settings: AppSettings) -> EcpVerifier:
    return build_verifier(settings)


def get_classifier(settings: AppSettings):
    """LLM-классификатор, если настроена локальная модель."""
    from salyq.categorize.llm import OpenAICompatibleClassifier

    if not settings.llm_base_url:
        return None
    return OpenAICompatibleClassifier(settings.llm_base_url, settings.llm_model, api_key=settings.llm_api_key)


def bearer_token(creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]) -> str:
    if creds is None:
        raise HTTPException(401, "нужен вход", headers={"WWW-Authenticate": "Bearer"})
    return creds.credentials


def current_user(session: DbSession, token: Annotated[str, Depends(bearer_token)]) -> User:
    user = user_by_token(session, token)
    if user is None:
        raise HTTPException(401, "сессия недействительна или истекла", headers={"WWW-Authenticate": "Bearer"})
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_consent(consent_type: str):
    """Зависимость: у пользователя есть действующее согласие этого типа (текущей редакции)."""

    def _check(session: DbSession, user: CurrentUser) -> User:
        if not consents.has_consent(session, user, consent_type):
            raise HTTPException(403, {"code": "CONSENT_REQUIRED", "consent": consent_type,
                                      "version": consents.CONSENT_VERSIONS[consent_type]})
        return user

    return _check


PdUser = Annotated[User, Depends(require_consent("pd_processing"))]
