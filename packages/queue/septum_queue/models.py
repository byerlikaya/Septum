"""Envelope dataclasses for cross-zone Septum messages.

These are the *only* payload shapes that cross the air-gap bridge. Every
field must be serializable with ``json.dumps`` so any backend (file,
Redis streams, RabbitMQ, HTTP) can persist and forward them without a
custom codec.

The envelopes are deliberately small: a ``RequestEnvelope`` carries the
sanitized chat-style messages plus enough provider hints for the gateway
to pick a concrete client; a ``ResponseEnvelope`` carries the masked
answer text plus provider metadata. Nothing in here references the
anonymization map, the session, or any raw document content — the
caller has already masked the payload before it reaches the queue.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


def _new_correlation_id() -> str:
    """Return a short correlation id used to pair a request with its reply."""
    return uuid.uuid4().hex


def _now() -> float:
    """Wall-clock timestamp; envelopes need this to survive cross-host transport."""
    return time.time()


@dataclass(frozen=True)
class Message:
    """Low-level queue envelope produced by :class:`QueueBackend.consume`.

    ``id`` is a backend-assigned identifier used to ack / nack the
    specific delivery (e.g. the Redis stream entry id, or the filename
    for the file backend). ``payload`` is the already-decoded JSON body
    supplied by the publisher.
    """

    id: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class RequestEnvelope:
    """Masked LLM request published by the api producer.

    The gateway consumer rebuilds a provider-specific HTTP call from
    these fields. ``messages`` is the already-sanitized OpenAI-style
    chat list. ``api_key`` is passed through so the gateway never needs
    to read api-side settings — in a split deployment the two zones
    have separate secrets stores.
    """

    correlation_id: str
    provider: str
    model: str
    messages: list[dict[str, str]]
    temperature: float = 0.2
    max_tokens: int | None = None
    api_key: str | None = None
    base_url: str | None = None
    created_at: float = field(default_factory=_now)

    @classmethod
    def new(
        cls,
        *,
        provider: str,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> "RequestEnvelope":
        """Convenience constructor that assigns a fresh correlation id."""
        return cls(
            correlation_id=_new_correlation_id(),
            provider=provider,
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key,
            base_url=base_url,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RequestEnvelope":
        return cls(
            correlation_id=str(data["correlation_id"]),
            provider=str(data["provider"]),
            model=str(data["model"]),
            messages=list(data.get("messages") or []),
            temperature=float(data.get("temperature", 0.2)),
            max_tokens=(
                int(data["max_tokens"]) if data.get("max_tokens") is not None else None
            ),
            api_key=data.get("api_key"),
            base_url=data.get("base_url"),
            created_at=float(data.get("created_at") or _now()),
        )


@dataclass(frozen=True)
class ResponseEnvelope:
    """Gateway reply paired with a request via ``correlation_id``.

    ``text`` holds the still-masked answer. ``error`` is set when the
    provider call failed so the producer can surface a distinct failure
    mode to its caller (and trigger the Ollama fallback path in the
    api when the gateway reports a cloud provider outage). The two
    fields are mutually exclusive — the gateway never publishes both.
    """

    correlation_id: str
    text: str | None = None
    error: str | None = None
    provider: str | None = None
    model: str | None = None
    created_at: float = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResponseEnvelope":
        return cls(
            correlation_id=str(data["correlation_id"]),
            text=data.get("text"),
            error=data.get("error"),
            provider=data.get("provider"),
            model=data.get("model"),
            created_at=float(data.get("created_at") or _now()),
        )
