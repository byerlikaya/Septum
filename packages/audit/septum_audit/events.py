"""The single audit envelope shape the package operates on.

PII discipline: ``attributes`` is JSON-serializable and must never
contain raw PII. Producers scrub before publishing — this package
cannot inspect for raw values; by contract every value it sees has
already passed through the air-gapped masking layer.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


def _new_id() -> str:
    return uuid.uuid4().hex


def _now() -> float:
    return time.time()


@dataclass(frozen=True)
class AuditRecord:
    """Immutable audit envelope."""

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
