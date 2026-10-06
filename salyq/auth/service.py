"""Вход по ЭЦП и сессии."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from salyq.auth import audit
from salyq.auth.clock import aware, utcnow
from salyq.auth.ecp import EcpVerificationError, EcpVerifier
from salyq.crypto import blind_index
from salyq.models import AuthChallenge, AuthSession, User
from salyq.settings import Settings


class AuthError(Exception):
    pass


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class LoginResult:
    user: User
    token: str
    expires_at: datetime
    is_new_user: bool


def create_challenge(session: Session, settings: Settings) -> tuple[str, datetime]:
    nonce = secrets.token_urlsafe(32)
    expires_at = utcnow() + timedelta(seconds=settings.ecp_challenge_ttl_seconds)
    session.add(AuthChallenge(nonce_hash=_sha256(nonce), expires_at=expires_at))
    session.commit()
    return nonce, expires_at


def _consume_challenge(session: Session, nonce: str) -> None:
    """Атомарно гасит nonce: из двух одновременных запросов пройдёт только один."""
    now = utcnow()
    consumed = session.execute(
        update(AuthChallenge)
        .where(
            AuthChallenge.nonce_hash == _sha256(nonce),
            AuthChallenge.used_at.is_(None),
            AuthChallenge.expires_at > now,
        )
        .values(used_at=now)
    ).rowcount
    if consumed != 1:
        raise AuthError("nonce не найден, уже использован или истёк")


def _fail(session: Session, reason: str) -> AuthError:
    session.rollback()
    audit.record(session, "auth.login_failed", actor_type="anonymous", reason=reason)
    session.commit()
    return AuthError(reason)


def login(
    session: Session, verifier: EcpVerifier, settings: Settings, *, nonce: str, signed_data: str
) -> LoginResult:
    try:
        _consume_challenge(session, nonce)
    except AuthError as exc:
        raise _fail(session, "bad_nonce") from exc
    try:
        subject = verifier.verify(signed_data, nonce)
    except EcpVerificationError as exc:
        # nonce остаётся использованным: повторить с ним нельзя
        session.commit()
        raise _fail(session, "bad_signature") from exc

    iin_hash = blind_index(subject.iin, "iin")
    user = session.scalar(select(User).where(User.iin_hash == iin_hash))
    is_new = user is None
    if user is None:
        user = User(iin=subject.iin, iin_hash=iin_hash, full_name=subject.full_name, employees_count=0)
        session.add(user)
        session.flush()
    elif user.full_name != subject.full_name:
        user.full_name = subject.full_name  # ФИО в новом сертификате могло измениться

    token = secrets.token_urlsafe(32)
    now = utcnow()
    expires_at = now + timedelta(hours=settings.session_ttl_hours)
    session.add(AuthSession(user_id=user.id, token_hash=_sha256(token), created_at=now, expires_at=expires_at))
    audit.record(session, "auth.login", actor_type="user", actor_id=user.id,
                 object_type="user", object_id=user.id, new_user=is_new)
    session.commit()
    return LoginResult(user, token, expires_at, is_new)


def _active_session(session: Session, token: str) -> AuthSession | None:
    s = session.scalar(select(AuthSession).where(AuthSession.token_hash == _sha256(token)))
    if s is None or s.revoked_at is not None or aware(s.expires_at) <= utcnow():
        return None
    return s


def user_by_token(session: Session, token: str) -> User | None:
    s = _active_session(session, token)
    return session.get(User, s.user_id) if s else None


def logout(session: Session, token: str) -> None:
    s = _active_session(session, token)
    if s is None:
        return
    s.revoked_at = utcnow()
    audit.record(session, "auth.logout", actor_type="user", actor_id=s.user_id)
    session.commit()
