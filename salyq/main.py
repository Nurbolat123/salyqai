from fastapi import FastAPI

from salyq.api import admin, auth, chat, consents, declarations, me, objections, privacy, statements, tax, transactions


def create_app() -> FastAPI:
    """Схему БД создают и меняют только миграции: `alembic upgrade head`."""
    app = FastAPI(title="Salyq AI — бэкенд", version="0.2.0")
    for r in (auth.router, me.router, consents.router, tax.router, privacy.router, statements.router, transactions.router,
              declarations.router, objections.router, admin.router, chat.router):
        app.include_router(r, prefix="/api/v1")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
