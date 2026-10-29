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
import math
import time
import uuid
from dataclasses import dataclass, field, fields, replace
from typing import Any, Collection, Iterable, Mapping

GENESIS_PREV_HASH = "0" * 64

# Set by the sink, and only by the sink, when it replaced a non-finite value
# (a timestamp or a nested attribute number). The sink drops any producer copy
# before writing, so producers cannot forge it.
INGEST_FLAG = "ingest_replaced"


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


def hash_of_dict(data: Mapping[str, Any]) -> str:
    """The hash of a record given as its ``to_dict()`` form. The single definition of the hash body."""
    return _hash_canonical({key: value for key, value in data.items() if key != "hash"})


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
    # Ingest time, stamped by the sink and covered by the hash. Retention ages
    # records by this value, so a producer's clock cannot pin a record in place.
    # ``None`` only for records written before the field existed.
    received_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        # Shallow on purpose: asdict() recurses into attributes, which overflows the
        # stack on deeply nested values. Callers that need a deep copy make one.
        data = {name: getattr(self, name) for name in _FIELD_NAMES}
        if data["received_at"] is None:
            # Omitted, not null: ledgers written before the field hashed without the key.
            del data["received_at"]
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), allow_nan=False)

    def expected_hash(self) -> str:
        """The hash this record must carry: sha256 over its canonical JSON without the ``hash`` field."""
        return hash_of_dict(self.to_dict())

    def with_hash_chain(self, *, prev_hash: str) -> "AuditRecord":
        """Return a copy stamped with ``prev_hash`` and a fresh ``hash``."""
        staged = replace(self, prev_hash=prev_hash, hash=None)
        return replace(staged, hash=staged.expected_hash())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AuditRecord":
        return cls(
            received_at=(
                float(data["received_at"]) if data.get("received_at") is not None else None
            ),
            id=str(data.get("id") or _new_id()),
            timestamp=_stored_timestamp(data.get("timestamp")),
            source=str(data.get("source") or "unknown"),
            event_type=str(data.get("event_type") or "unknown"),
            correlation_id=(
                str(data["correlation_id"])
                if data.get("correlation_id") is not None
                else None
            ),
            # Kept as given: the sink normalises any shape, so a bad shape cannot fail the parse.
            attributes=data.get("attributes") if data.get("attributes") is not None else {},
            prev_hash=(
                str(data["prev_hash"]) if data.get("prev_hash") is not None else None
            ),
            hash=str(data["hash"]) if data.get("hash") is not None else None,
        )


_FIELD_NAMES: tuple[str, ...] = tuple(f.name for f in fields(AuditRecord))


class UnstorableRecordError(ValueError):
    """A record would not verify once read back, so it is not written."""


INGEST_UNSTORABLE = "ingest_unstorable"


# The sink sets INGEST_UNSTORABLE only for an UnstorablePlaceholder it is given, so a producer cannot forge it.
class UnstorablePlaceholder(AuditRecord):
    """The consumer's stand-in for a record the sink refused. Only the sink's write path marks it."""


def _stored_timestamp(raw: Any) -> float:
    """The timestamp a record carries: a finite, non-zero float, or NaN.

    NaN marks a value the sink must replace with the ingest time. That covers
    anything that is not a real int or float (bool, string, None), zero, a
    non-finite number and an int too large for a float.
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return math.nan
    try:
        value = float(raw)
    except OverflowError:
        return math.nan
    return value if math.isfinite(value) and value != 0 else math.nan


def normalise_attributes(value: Any) -> tuple[dict[str, Any], bool]:
    """Normalise ``attributes`` to the shape its serialised line reads back as.

    - A mapping keeps its items, with every key made a string.
    - A list of two-item pairs becomes a dict, with each key made a string.
    - Any other value becomes ``{"value": ...}``.
    - At the top level, if two keys collide once made strings, every pair is kept as a
      list under ``"value"``, so nothing is lost. Nested collisions keep the last value.
    - A value that cannot be serialised becomes ``{"value": "<unserialisable>"}``.

    The result is what ``json.loads`` returns for its serialised form, so a hash computed
    over it matches on read-back. Returns ``(attributes, changed)``.
    """
    if isinstance(value, Mapping):
        pairs: list[tuple[Any, Any]] | None = list(value.items())
    elif isinstance(value, (list, tuple)) and all(
        isinstance(item, (list, tuple)) and len(item) == 2 for item in value
    ):
        pairs = [(item[0], item[1]) for item in value]
    else:
        pairs = None
    if pairs is None:
        candidate: Any = {"value": value}
    else:
        as_dict = {str(key): item for key, item in pairs}
        candidate = as_dict if len(as_dict) == len(pairs) else {"value": [[str(k), v] for k, v in pairs]}
    try:
        canonical = json.loads(json.dumps(json_safe(candidate)[0], allow_nan=False))
    except (TypeError, ValueError, RecursionError):
        canonical = {"value": "<unserialisable>"}
    return canonical, not (isinstance(value, dict) and canonical == value)


def loads_strict(text: str) -> dict[str, Any]:
    """``json.loads`` that rejects duplicate keys.

    Duplicate keys make the parsed record depend on which occurrence wins,
    so a line that the hash does not cover could still change meaning.
    """

    def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key in ledger line: {key}")
            result[key] = value
        return result

    payload = json.loads(text, object_pairs_hook=_no_duplicates)
    if not isinstance(payload, dict):
        raise ValueError("ledger line is not a JSON object")
    return payload


def parse_line(text: str) -> dict[str, Any]:
    """Parse one ledger line. Nesting deeper than the parser allows is a ``ValueError``."""
    try:
        return loads_strict(text)
    except RecursionError:
        raise ValueError("ledger line is too deeply nested") from None


def parse_record(text: str) -> tuple[dict[str, Any], "AuditRecord"]:
    """Parse one ledger line into its raw payload and record, with the same error contract as :func:`parse_line`."""
    payload = parse_line(text)
    try:
        return payload, AuditRecord.from_dict(payload)
    except (OverflowError, RecursionError, TypeError, AttributeError):
        raise ValueError("ledger record holds a value of the wrong type or out of range") from None


def known_age_reference(payload: Mapping[str, Any]) -> float | None:
    """The age retention measures a raw ledger line from, or ``None`` when that age is unknown.

    ``received_at`` wins when present. Otherwise ``timestamp`` is used, and a
    missing, zero or non-finite timestamp leaves the age unknown.
    """
    try:
        received = payload.get("received_at")
        if received is not None:
            return float(received)
        raw = payload.get("timestamp")
        if raw is None:
            return None
        value = float(raw)
    except (OverflowError, TypeError):
        raise ValueError("ledger line holds a value that is not a number in range") from None
    return value if math.isfinite(value) and value != 0 else None


def short_id(value: object) -> str:
    """A producer-supplied id, quoted and cut to 64 characters, for logs and error text."""
    return repr(str(value)[:64])


DEPTH_LIMIT = 32
DEPTH_LIMIT_MARKER = "<depth limit>"


def _is_container(value: Any) -> bool:
    """True for the JSON containers that json_safe copies: dict, list and tuple."""
    return isinstance(value, (dict, list, tuple))


def _empty_like(value: Any) -> dict[Any, Any] | list[Any]:
    """An empty dict for a dict, otherwise an empty list, so tuples are copied as lists."""
    return {} if isinstance(value, dict) else []


def _json_safe_leaf(item: Any) -> tuple[Any, bool]:
    """Replace a non-finite float with its text. Returns ``(value, changed)``."""
    if isinstance(item, float) and not math.isfinite(item):
        if math.isnan(item):
            return "NaN", True
        return ("Infinity" if item > 0 else "-Infinity"), True
    return item, False


def _json_safe_enter(
    item: Any,
    depth: int,
    max_depth: int | None,
    stack: list[tuple[Any, Any, int]],
) -> tuple[Any, bool]:
    """Copy one child. A nested container is replaced by its empty copy and pushed onto ``stack`` to be filled, or by the depth marker past the cap."""
    if not _is_container(item):
        return _json_safe_leaf(item)
    if max_depth is not None and depth + 1 > max_depth:
        return DEPTH_LIMIT_MARKER, True
    replaced = _empty_like(item)
    stack.append((item, replaced, depth + 1))
    return replaced, False


def json_safe(value: Any, max_depth: int | None = DEPTH_LIMIT) -> tuple[Any, bool]:
    """Copy a JSON-like value so it can be serialised with ``allow_nan=False``.

    Non-finite floats become their string form ("NaN", "Infinity", "-Infinity").
    When ``max_depth`` is set, containers nested deeper than it are replaced by
    ``DEPTH_LIMIT_MARKER``; ``None`` applies no depth cap (exports). Tuples become
    lists. Iterative, so depth cannot overflow the stack. Returns ``(copy, changed)``.
    """
    if not _is_container(value):
        return _json_safe_leaf(value)
    changed = False
    root = _empty_like(value)
    stack: list[tuple[Any, Any, int]] = [(value, root, 0)]
    while stack:
        source, target, depth = stack.pop()
        items = source.items() if isinstance(source, dict) else enumerate(source)
        for key, item in items:
            replaced, item_changed = _json_safe_enter(item, depth, max_depth, stack)
            changed = changed or item_changed
            if isinstance(target, dict):
                target[key] = replaced
            else:
                target.append(replaced)
    return root, changed


def dumps_safe(value: Any, max_depth: int | None = DEPTH_LIMIT) -> str:
    """Serialise any JSON-like value as strict JSON (no NaN or Infinity literals)."""
    safe, _ = json_safe(value, max_depth)
    return json.dumps(safe, separators=(",", ":"), allow_nan=False)


def require_count(value: object) -> None:
    """Raise ``ValueError`` unless ``value`` is a non-negative integer (``bool`` is not one)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("not a non-negative integer")


def require_finite_number(value: object) -> None:
    """Raise ``ValueError`` unless ``value`` is a finite int or float (``bool`` is not one)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("not a finite number")


class LedgerIntegrityError(ValueError):
    """A ledger line or chain link is wrong. Retention refuses to prune until it is repaired."""


def verify_chain(
    records: Iterable["AuditRecord"],
    *,
    accepted_first_prev: Collection[str] = (GENESIS_PREV_HASH,),
) -> None:
    """Walk an iterable of records and raise if the chain is broken.

    The first record must link to one of ``accepted_first_prev``: genesis
    for an unpruned ledger, or the retention anchor for a pruned one. Every
    later record must link to its predecessor's hash.

    Used by exporters and tests to assert tamper-evidence. Operators
    can also wire this into their forwarder pipeline before pushing
    to a SIEM so silent corruption is detected at ship time.
    """
    accepted = frozenset(accepted_first_prev)
    previous_hash: str | None = None
    for index, record in enumerate(records):
        linked = (
            record.prev_hash in accepted
            if index == 0
            else record.prev_hash == previous_hash
        )
        if not linked:
            raise ValueError(
                f"audit chain broken at index {index}: prev_hash mismatch"
            )
        if record.hash != record.expected_hash():
            raise ValueError(
                f"audit chain broken at index {index}: hash mismatch (record {short_id(record.id)})"
            )
        previous_hash = record.hash
