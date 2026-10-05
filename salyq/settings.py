from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SALYQ_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://salyq:salyq@localhost:5432/salyq"
    max_upload_bytes: int = 20 * 1024 * 1024
    # Ключи шифрования полей и слепых индексов: base64 от 32 случайных байт.
    # В проде — из KMS / Vault в РК; значения по умолчанию отсутствуют намеренно.
    field_key: str | None = None
    hmac_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
