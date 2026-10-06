from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from salyq.api import admin, auth, chat, consents, declarations, me, objections, privacy, statements, tax, transactions


def create_app() -> FastAPI:
    """Схему БД создают и меняют только миграции: `alembic upgrade head`."""
    from salyq import observability
    from salyq.settings import get_settings

    settings = get_settings()
    app = FastAPI(title="Salyq AI — бэкенд", version="0.3.0")
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"])
    observability.setup(app, settings)
    for r in (auth.router, me.router, consents.router, tax.router, privacy.router, statements.router, transactions.router,
              declarations.router, objections.router, admin.router, chat.router):
        app.include_router(r, prefix="/api/v1")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
