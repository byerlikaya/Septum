"""Prometheus metrics for Septum API."""

import time
from typing import Callable

from fastapi import Request, Response
from prometheus_client import Counter, Histogram, Info, generate_latest, CONTENT_TYPE_LATEST
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse

REQUEST_COUNT = Counter(
    "septum_http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)

REQUEST_LATENCY = Histogram(
    "septum_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

DOCUMENTS_UPLOADED = Counter(
    "septum_documents_uploaded_total",
    "Total documents uploaded",
)

CHAT_REQUESTS = Counter(
    "septum_chat_requests_total",
    "Total chat requests",
)

PII_ENTITIES_DETECTED = Counter(
    "septum_pii_entities_detected_total",
    "Total PII entities detected",
    ["entity_type"],
)

APP_INFO = Info(
    "septum",
    "Septum application info",
)
APP_INFO.info({"version": "1.0.0"})


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Middleware that records request count and latency for Prometheus."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path == "/metrics":
            return await call_next(request)

        method = request.method
        path = self._normalize_path(request.url.path)
        start = time.perf_counter()

        response = await call_next(request)

        elapsed = time.perf_counter() - start
        status = str(response.status_code)

        REQUEST_COUNT.labels(method=method, path=path, status=status).inc()
        REQUEST_LATENCY.labels(method=method, path=path).observe(elapsed)

        return response

    @staticmethod
    def _normalize_path(path: str) -> str:
        """Replace dynamic path segments with placeholders to limit cardinality."""
        parts = path.strip("/").split("/")
        normalized = []
        for part in parts:
            if part.isdigit():
                normalized.append("{id}")
            else:
                normalized.append(part)
        return "/" + "/".join(normalized)


def metrics_endpoint(_request: Request) -> StarletteResponse:
    """Endpoint handler that returns Prometheus metrics."""
    return StarletteResponse(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
