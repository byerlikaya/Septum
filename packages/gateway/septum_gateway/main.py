"""FastAPI entrypoint for the gateway process.

The gateway runs as a long-lived process that (a) exposes a minimal
``/health`` endpoint for operator monitoring and (b) drives a
:class:`GatewayConsumer` loop against a configured queue backend.
Phase 5 ships the scaffolding; the actual deployment wiring (Dockerfile
+ compose variant) lands in Phase 7.

FastAPI / uvicorn are optional: the ``[server]`` extra pulls them in.
The rest of this package (forwarders, consumer, config) works without
a web server so an operator running a bare ``python -m
septum_gateway`` worker script does not need to install fastapi.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import GatewayConfig

logger = logging.getLogger(__name__)


def _import_fastapi():
    try:
        from fastapi import FastAPI
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "septum-gateway[server] is required to run the FastAPI app. "
            "Install with: pip install 'septum-gateway[server]'"
        ) from exc
    return FastAPI


def create_app(config: GatewayConfig | None = None) -> Any:
    """Build the FastAPI app. Kept as a factory so tests can pass a fixed config."""
    FastAPI = _import_fastapi()
    cfg = config or GatewayConfig.from_env()

    app = FastAPI(
        title="Septum Gateway",
        description=(
            "Internet-facing LLM forwarder for Septum. Consumes masked "
            "requests from septum-queue and dispatches them to Anthropic / "
            "OpenAI / OpenRouter. Never sees raw PII."
        ),
        version="0.1.0",
    )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        # Deliberately minimal — the gateway has no database, no
        # session store, and no PII it can leak via a health probe.
        return {
            "status": "ok",
            "service": "septum-gateway",
            "request_topic": cfg.request_topic,
            "response_topic": cfg.response_topic,
        }

    return app
