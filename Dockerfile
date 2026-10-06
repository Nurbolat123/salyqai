FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml ./
COPY salyq ./salyq
RUN pip install ".[dev]"
COPY alembic.ini ./
COPY migrations ./migrations
COPY tests ./tests

RUN useradd --create-home --uid 1000 salyq
USER salyq

EXPOSE 8000
# Сначала миграции, затем API
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn salyq.main:app --host 0.0.0.0 --port 8000"]
