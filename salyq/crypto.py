"""Шифрование полей с персональными данными (ТЗ, раздел 6) и «слепые» индексы.

AES-256-GCM, ключ из настроек (в проде — из KMS / Vault в РК). Формат значения:
"v1:" + base64(nonce ‖ ciphertext). Префикс версии оставляет место для ротации ключей.
Для поиска и дедупликации по зашифрованным полям используется HMAC-SHA256 с
отдельным ключом: он не раскрывает значение и устойчив к перебору (в отличие
от простого sha256 от 12-значного ИИН).
"""

import base64
import hashlib
import hmac
import os
from functools import lru_cache

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from salyq.settings import get_settings

_PREFIX = "v1:"


class CryptoConfigError(RuntimeError):
    pass


def _key(name: str) -> bytes:
    raw = getattr(get_settings(), name)
    if not raw:
        raise CryptoConfigError(f"не задан SALYQ_{name.upper()} (base64, 32 байта)")
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise CryptoConfigError(f"SALYQ_{name.upper()} должен быть 32 байта")
    return key


@lru_cache
def _aead() -> AESGCM:
    return AESGCM(_key("field_key"))


@lru_cache
def _hmac_key() -> bytes:
    return _key("hmac_key")


def encrypt(plaintext: str) -> str:
    nonce = os.urandom(12)
    return _PREFIX + base64.b64encode(nonce + _aead().encrypt(nonce, plaintext.encode(), None)).decode()


def decrypt(token: str) -> str:
    if not token.startswith(_PREFIX):
        raise ValueError("неизвестный формат шифртекста")
    blob = base64.b64decode(token[len(_PREFIX):])
    return _aead().decrypt(blob[:12], blob[12:], None).decode()


def blind_index(value: str, purpose: str) -> str:
    """Детерминированный HMAC для поиска/уникальности; purpose разделяет домены."""
    return hmac.new(_hmac_key(), f"{purpose}\x1f{value}".encode(), hashlib.sha256).hexdigest()


def mask_iban(iban: str) -> str:
    return f"{iban[:4]}…{iban[-4:]}" if len(iban) > 8 else "…"


class EncryptedText(TypeDecorator):
    """Колонка, которая прозрачно шифруется при записи и расшифровывается при чтении."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else encrypt(value)

    def process_result_value(self, value, dialect):
        return None if value is None else decrypt(value)
