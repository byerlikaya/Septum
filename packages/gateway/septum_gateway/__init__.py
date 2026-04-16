"""Septum gateway: internet-facing LLM forwarder.

The gateway consumes masked chat requests from ``septum-queue`` and
dispatches them to cloud LLM providers (Anthropic, OpenAI, OpenRouter),
then publishes the masked answers back onto a reply topic. It never
imports ``septum-core`` — that wall is what keeps raw PII out of the
internet-facing zone by code-review invariant, not just by deployment
convention.

The FastAPI ``create_app`` helper is optional and gated behind the
``[server]`` extra so an operator running a bare
``python -m septum_gateway.worker`` script does not need to pull
``fastapi`` / ``uvicorn``.
"""

from __future__ import annotations

from typing import Any

from .config import GatewayConfig
from .forwarder import (
    AnthropicForwarder,
    BaseForwarder,
    ForwarderLike,
    ForwarderRegistry,
    GatewayError,
    OpenAIForwarder,
    OpenRouterForwarder,
)
from .response_handler import GatewayConsumer

__all__ = [
    "GatewayConfig",
    "GatewayConsumer",
    "GatewayError",
    "ForwarderLike",
    "BaseForwarder",
    "ForwarderRegistry",
    "AnthropicForwarder",
    "OpenAIForwarder",
    "OpenRouterForwarder",
    "create_app",
]

__version__ = "0.1.0"


def __getattr__(name: str) -> Any:
    """Lazy ``create_app`` so the base package does not import fastapi."""
    if name == "create_app":
        from .main import create_app

        return create_app
    raise AttributeError(f"module 'septum_gateway' has no attribute {name!r}")
