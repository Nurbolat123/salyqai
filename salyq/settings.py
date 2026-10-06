from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SALYQ_", env_file=".env", extra="ignore")

    environment: Literal["dev", "test", "prod"] = "dev"
    database_url: str = "postgresql+psycopg://salyq:salyq@localhost:5432/salyq"
    max_upload_bytes: int = 20 * 1024 * 1024
    # Ключи шифрования полей и слепых индексов: base64 от 32 случайных байт.
    # В проде — из KMS / Vault в РК; значения по умолчанию отсутствуют намеренно.
    field_key: str | None = None
    hmac_key: str | None = None

    # Проверка подписи ЭЦП: "dev" — заглушка без криптографии, только для разработки
    ecp_verifier: Literal["dev", "ncanode"] = "dev"
    ecp_challenge_ttl_seconds: int = 300
    session_ttl_hours: int = 12

    # Локальная LLM (vLLM, OpenAI-совместимый API) в ЦОДе РК. Не задано — разметка только правилами.
    llm_base_url: str | None = None
    llm_model: str = "local-model"
    llm_api_key: str | None = None

    # Внешняя LLM для чата — только с согласием на трансграничную передачу и через
    # шлюз обезличивания; только провайдер с zero data retention (ТЗ 6)
    external_llm_base_url: str | None = None
    external_llm_model: str = ""
    external_llm_api_key: str | None = None

    # Напоминания (ТЗ 4.6)
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_sender: str = "Salyq <noreply@salyq.kz>"
    smtp_user: str | None = None
    smtp_password: str | None = None
    telegram_bot_token: str | None = None
    redis_url: str = "redis://localhost:6379/0"

    # Пилот: эксперт проверяет каждую 910.00 до подписи (ТЗ 7)
    declaration_expert_review: bool = True

    @model_validator(mode="after")
    def _no_dev_auth_in_prod(self) -> "Settings":
        if self.environment == "prod" and self.ecp_verifier == "dev":
            raise ValueError("SALYQ_ECP_VERIFIER=dev запрещён при SALYQ_ENVIRONMENT=prod")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
