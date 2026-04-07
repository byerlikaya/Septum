"""Prometheus metrics for Septum API."""

import time
from typing import Any

from fastapi import Request
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    Info,
    generate_latest,
)
from starlette.responses import Response as StarletteResponse
from starlette.types import ASGIApp, Receive, Scope, Send

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


class PrometheusMiddleware:
    """Pure ASGI middleware that records request count and latency.

    Uses the raw ASGI interface instead of ``BaseHTTPMiddleware`` to avoid
    the known Starlette issue where ``BaseHTTPMiddleware`` cancels
    long-lived streaming connections (SSE).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path == "/metrics":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        normalized_path = self._normalize_path(path)
        start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 500)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - start
            REQUEST_COUNT.labels(
                method=method, path=normalized_path, status=str(status_code)
            ).inc()
            REQUEST_LATENCY.labels(
                method=method, path=normalized_path
            ).observe(elapsed)

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
