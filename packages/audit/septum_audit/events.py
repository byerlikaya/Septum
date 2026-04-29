"""The single audit envelope shape the package operates on.

PII discipline: ``attributes`` is JSON-serializable and must never
contain raw PII. Producers scrub before publishing — this package
cannot inspect for raw values; by contract every value it sees has
already passed through the air-gapped masking layer.

Tamper evidence: each record carries ``prev_hash`` and ``hash`` so
the ledger forms a Merkle-style chain. ``prev_hash`` is the hash of
the previously written record (or 64 zeros for the genesis entry).
``hash`` is sha256 over the canonical JSON of the record itself
(excluding the ``hash`` field). A verifier walks the chain top-down
and detects any post-write edit / reordering / deletion.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Mapping


_GENESIS_HASH = "0" * 64


def _new_id() -> str:
    return uuid.uuid4().hex


def _now() -> float:
    return time.time()


def _hash_canonical(payload: Mapping[str, Any]) -> str:
    """Return sha256 over the canonical JSON of ``payload``.

    Sorts keys + uses compact separators so the hash does not depend
    on Python dict iteration order. The ``hash`` field itself is
    excluded by the caller so ``hash(record_minus_hash)`` is stable
    regardless of when the field was filled in.
    """
    canonical = json.dumps(
        dict(payload), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class AuditRecord:
    """Immutable audit envelope."""

    id: str = field(default_factory=_new_id)
    timestamp: float = field(default_factory=_now)
    source: str = "unknown"
    event_type: str = "unknown"
    correlation_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    # Hash chain. Sinks set both fields when the record is appended
    # so already-constructed records can flow through the consumer
    # without callers having to know about the chain protocol.
    prev_hash: str | None = None
    hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    def with_hash_chain(self, *, prev_hash: str) -> "AuditRecord":
        """Return a copy stamped with ``prev_hash`` and a fresh ``hash``."""
        staged = replace(self, prev_hash=prev_hash, hash=None)
        body = staged.to_dict()
        body.pop("hash", None)
        return replace(staged, hash=_hash_canonical(body))

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
            prev_hash=(
                str(data["prev_hash"]) if data.get("prev_hash") is not None else None
            ),
            hash=str(data["hash"]) if data.get("hash") is not None else None,
        )


GENESIS_PREV_HASH = _GENESIS_HASH


def verify_chain(records: Iterable["AuditRecord"]) -> None:
    """Walk an iterable of records and raise if the chain is broken.

    Used by exporters and tests to assert tamper-evidence. Operators
    can also wire this into their forwarder pipeline before pushing
    to a SIEM so silent corruption is detected at ship time.
    """
    expected_prev = _GENESIS_HASH
    for index, record in enumerate(records):
        if record.prev_hash != expected_prev:
            raise ValueError(
                f"audit chain broken at index {index}: prev_hash mismatch"
            )
        body = record.to_dict()
        body.pop("hash", None)
        expected_hash = _hash_canonical(body)
        if record.hash != expected_hash:
            raise ValueError(
                f"audit chain broken at index {index}: hash mismatch (record {record.id!r})"
            )
        expected_prev = record.hash


from typing import Iterable  # noqa: E402  (placed after verify_chain forward ref)
