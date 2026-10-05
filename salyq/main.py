from contextlib import asynccontextmanager

from fastapi import FastAPI

from salyq.api import privacy, statements, tax


@asynccontextmanager
async def lifespan(app: FastAPI):
    # MVP: схема создаётся при старте. Следующий шаг — миграции Alembic.
    from salyq import models  # noqa: F401
    from salyq.db import Base, get_engine

    Base.metadata.create_all(get_engine())
    yield


def create_app(*, init_db: bool = True) -> FastAPI:
    app = FastAPI(title="Salyq AI — бэкенд", version="0.1.0", lifespan=lifespan if init_db else None)
    for r in (tax.router, privacy.router, statements.router):
        app.include_router(r, prefix="/api/v1")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
