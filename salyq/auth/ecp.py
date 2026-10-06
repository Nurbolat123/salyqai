"""Проверка подписи ЭЦП при входе (ТЗ 4.1).

Поток: сервер выдаёт одноразовый nonce → приложение подписывает его ключом ЭЦП
через NCALayer или eGov Mobile (CMS) → сервер проверяет подпись и цепочку
сертификата и берёт ИИН и ФИО из сертификата.

Проверка спрятана за интерфейсом EcpVerifier:
- DevEcpVerifier — заглушка без криптографии для разработки и тестов;
- NcaNodeVerifier — проверка через NCANode (сервис проверки подписей НУЦ РК),
  будет реализована при подключении к тестовому контуру eGov.
"""

import base64
import json
from dataclasses import dataclass
from typing import Protocol

from salyq.privacy.validators import kz_id_kind
from salyq.settings import Settings


class EcpVerificationError(Exception):
    pass


@dataclass(frozen=True)
class CertSubject:
    iin: str
    full_name: str


class EcpVerifier(Protocol):
    def verify(self, signed_data: str, nonce: str) -> CertSubject:
        """Проверить подпись nonce; вернуть владельца сертификата или EcpVerificationError."""
        ...


class DevEcpVerifier:
    """НЕ проверяет криптографию. signed_data — base64(JSON {"nonce", "iin", "full_name"}).
    Запрещён при SALYQ_ENVIRONMENT=prod (см. Settings)."""

    def verify(self, signed_data: str, nonce: str) -> CertSubject:
        try:
            payload = json.loads(base64.b64decode(signed_data, validate=True))
            iin, full_name, signed_nonce = payload["iin"], payload["full_name"], payload["nonce"]
        except (ValueError, KeyError, TypeError) as exc:
            raise EcpVerificationError("некорректные подписанные данные") from exc
        if signed_nonce != nonce:
            raise EcpVerificationError("подписан другой nonce")
        if not isinstance(iin, str) or kz_id_kind(iin) != "IIN":
            raise EcpVerificationError("в сертификате нет корректного ИИН")
        if not isinstance(full_name, str) or not full_name.strip():
            raise EcpVerificationError("в сертификате нет ФИО")
        return CertSubject(iin=iin, full_name=" ".join(full_name.split()))

    @staticmethod
    def sign(nonce: str, iin: str, full_name: str) -> str:
        """Помощник для тестов и локальной разработки."""
        return base64.b64encode(json.dumps({"nonce": nonce, "iin": iin, "full_name": full_name}).encode()).decode()


class NcaNodeVerifier:
    def __init__(self, settings: Settings) -> None:
        raise NotImplementedError(
            "проверка ЭЦП через NCANode ещё не реализована — нужен доступ к тестовому контуру"
        )

    def verify(self, signed_data: str, nonce: str) -> CertSubject:  # pragma: no cover
        raise NotImplementedError


def build_verifier(settings: Settings) -> EcpVerifier:
    return DevEcpVerifier() if settings.ecp_verifier == "dev" else NcaNodeVerifier(settings)
