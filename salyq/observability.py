"""Наблюдаемость (ТЗ 3): метрики Prometheus и Sentry (self-hosted в РК).

В метки и события не попадают тела запросов и ПДн: только шаблон пути, метод, код.
"""

import time

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from salyq.settings import Settings

REQUESTS = Counter("salyq_http_requests_total", "HTTP-запросы", ["method", "route", "status"])
LATENCY = Histogram("salyq_http_request_seconds", "Время ответа", ["method", "route"],
                    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 10, 30, 120))


def setup(app: FastAPI, settings: Settings) -> None:
    @app.middleware("http")
    async def metrics(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        template = getattr(route, "path", "unmatched")
        REQUESTS.labels(request.method, template, str(response.status_code)).inc()
        LATENCY.labels(request.method, template).observe(time.perf_counter() - start)
        return response

    @app.get("/metrics", include_in_schema=False)
    def prometheus() -> Response:
        # Закрыть от внешнего мира на уровне ingress — только для Prometheus внутри ЦОДа
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    if settings.sentry_dsn:
        import sentry_sdk

        sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.environment,
                        send_default_pii=False, max_request_body_size="never", traces_sample_rate=0.0)
