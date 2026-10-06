"""Проверка подписи ЭЦП при входе (ТЗ 4.1).

Поток: сервер выдаёт одноразовый nonce → приложение подписывает его ключом ЭЦП
через NCALayer или eGov Mobile (CMS) → сервер проверяет подпись и цепочку
сертификата и берёт ИИН и ФИО из сертификата.

Проверка спрятана за интерфейсом EcpVerifier:
- DevEcpVerifier — заглушка без криптографии для разработки и тестов;
- NcaNodeVerifier — проверка через NCANode (открытый сервер проверки подписей НУЦ РК,
  разворачивается у нас; для тестов — тестовые ключи и корневые сертификаты с pki.gov.kz).
"""

import base64
import json
from dataclasses import dataclass
from typing import Protocol

from salyq.http import post_json
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
    """Проверка CMS-подписи через NCANode (REST API v3).

    Клиент подписывает nonce в NCALayer (createCMSSignatureFromBase64, attached).
    Сервер:
    1. /cms/verify — подпись математически верна, цепочка до корневого НУЦ, отзыв по OCSP;
    2. /cms/extract — подписанные данные совпадают с выданным nonce;
    3. из сертификата подписанта берёт ИИН и ФИО.

    ВНИМАНИЕ: названия полей ответа сверить с версией NCANode при подключении;
    адаптер ищет ИИН и ФИО по нескольким вариантам названий.
    """

    def __init__(self, settings: Settings) -> None:
        if not settings.ncanode_url:
            raise ValueError("для SALYQ_ECP_VERIFIER=ncanode нужен SALYQ_NCANODE_URL")
        self.url = settings.ncanode_url.rstrip("/")
        self.timeout = settings.ncanode_timeout_seconds

    def _post(self, path: str, body: dict) -> dict:
        try:
            return post_json(self.url + path, body, timeout=self.timeout)
        except Exception as exc:
            raise EcpVerificationError(f"NCANode недоступен: {exc}") from exc

    @staticmethod
    def _subject(cert: dict) -> CertSubject:
        subj = cert.get("subject") or {}
        iin = subj.get("iin") or subj.get("IIN") or ""
        if iin.upper().startswith("IIN"):
            iin = iin[3:]
        parts = [subj.get(k) for k in ("lastName", "surName", "surname") if subj.get(k)][:1]
        parts += [subj.get(k) for k in ("firstName", "givenName", "commonName") if subj.get(k)][:1]
        parts += [subj.get(k) for k in ("middleName", "patronymic") if subj.get(k)][:1]
        name = " ".join(parts) if len(parts) >= 2 else subj.get("commonName", "")
        if kz_id_kind(iin) != "IIN":
            raise EcpVerificationError("в сертификате нет ИИН физического лица")
        if not name.strip():
            raise EcpVerificationError("в сертификате нет ФИО")
        return CertSubject(iin=iin, full_name=" ".join(name.split()))

    def verify(self, signed_data: str, nonce: str) -> CertSubject:
        result = self._post("/cms/verify", {"cms": signed_data, "revocationCheck": ["OCSP"]})
        if not result.get("valid"):
            raise EcpVerificationError(f"подпись недействительна: {result.get('message', '')}")
        signers = result.get("signers") or []
        if len(signers) != 1:
            raise EcpVerificationError("ожидается ровно один подписант")
        certs = signers[0].get("certificates") or []
        if not certs or not all(c.get("valid", False) for c in certs):
            raise EcpVerificationError("сертификат подписанта недействителен или отозван")
        extracted = self._post("/cms/extract", {"cms": signed_data})
        try:
            data = base64.b64decode(extracted.get("data") or "").decode()
        except (ValueError, UnicodeDecodeError) as exc:
            raise EcpVerificationError("не удалось извлечь подписанные данные") from exc
        if data != nonce:
            raise EcpVerificationError("подписаны не те данные")
        return self._subject(certs[0])


def build_verifier(settings: Settings) -> EcpVerifier:
    return DevEcpVerifier() if settings.ecp_verifier == "dev" else NcaNodeVerifier(settings)
