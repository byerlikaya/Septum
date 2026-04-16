"""The single audit envelope shape the entire package operates on.

Every event written to a sink, exported to a file, or read off the queue
becomes an :class:`AuditRecord`. Keeping the shape narrow (one ID, one
timestamp, one source string, one event_type, an opaque attributes map)
keeps the exporters dumb — they iterate records and serialize the same
fields regardless of which producer emitted them.

PII discipline: the ``attributes`` map is *not* free-form for downstream
producers. Gateways and api producers must scrub it before publishing.
This package never inspects attributes for raw values; it cannot — by
contract every value it sees has already passed through the air-gapped
masking layer.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


def _new_id() -> str:
    """Short opaque id used to dedupe across exporter retries."""
    return uuid.uuid4().hex


def _now() -> float:
    """Wall-clock unix timestamp (records cross host boundaries)."""
    return time.time()


@dataclass(frozen=True)
class AuditRecord:
    """Immutable audit envelope.

    ``source`` names the emitting service (``"septum-gateway"``,
    ``"septum-api"``, etc.). ``event_type`` is a free-form short string
    the operator filters on (``"llm.request.completed"``,
    ``"pii.detected"``). ``attributes`` is JSON-serializable and must
    never contain raw PII — producers scrub before publishing.
    """

    id: str = field(default_factory=_new_id)
    timestamp: float = field(default_factory=_now)
    source: str = "unknown"
    event_type: str = "unknown"
    correlation_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AuditRecord":
        return cls(
            id=str(data.get("id") or _new_id()),
            timestamp=float(data.get("timestamp") or _now()),
            source=str(data.get("source") or "unknown"),
            event_type=str(data.get("event_type") or "unknown"),
            correlation_id=(
                str(data["correlation_id"])
                if data.get("correlation_id") is not None
                else None
            ),
            attributes=dict(data.get("attributes") or {}),
        )
