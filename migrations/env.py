"""Окружение Alembic. URL базы берётся из SALYQ_DATABASE_URL (как у приложения)."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from salyq import models  # noqa: F401  — регистрирует таблицы в Base.metadata
from salyq.db import Base
from salyq.settings import get_settings

config = context.config
# При вызове из кода (тесты) логирование приложения не перенастраиваем
if config.config_file_name is not None and "connection" not in config.attributes:
    fileConfig(config.config_file_name)
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata,
        literal_binds=True, dialect_opts={"paramstyle": "named"}, compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is None:
        connectable = engine_from_config(
            config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool
        )
        with connectable.connect() as connection:
            _run(connection)
    else:
        _run(connectable)


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
