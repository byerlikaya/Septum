"""Age- and count-based retention for jsonl sinks."""

from __future__ import annotations

import dataclasses
import json
import math
import os
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Iterator

from .anchor import (
    RetentionAnchor,
    accepted_links,
    anchor_path,
    new_anchor,
    read_anchor,
    write_anchor,
)
from .events import (
    GENESIS_PREV_HASH,
    AuditRecord,
    LedgerIntegrityError,
    hash_of_dict,
    known_age_reference,
    parse_record,
    short_id,
)
from .fsutil import create_tmp, fsync_dir, locked, nonblank, open_ledger_file, sibling

_RECORD_FIELDS = frozenset(f.name for f in dataclasses.fields(AuditRecord))
_READ_BLOCK = 1 << 20
_COUNT_SLACK_FRACTION = 0.05
# A record stamped further ahead of the clock than this pins pruning.
FUTURE_PIN_SECONDS = 300.0


@dataclass(frozen=True)
class RetentionPolicy:
    """Age cap and/or count cap. ``None`` disables that dimension.

    The age cutoff is ``now - max_age_days``. A record is removed by the first pass
    that starts after its cutoff. A line with no usable age takes the age of the next
    line that has one, and is kept when no such line follows.

    The count cap prunes only once the ledger exceeds ``max_records`` by the slack
    (5 %, at least 1), and then trims back to exactly ``max_records``. Between passes
    the ledger can hold more than that.
    """

    max_age_days: int | None = None
    max_records: int | None = None

    def __post_init__(self) -> None:
        for name, value in (("max_age_days", self.max_age_days), ("max_records", self.max_records)):
            if value is not None and (not isinstance(value, int) or value < 1):
                raise ValueError(f"RetentionPolicy.{name} must be a whole number of at least 1")

    @property
    def is_noop(self) -> bool:
        return self.max_age_days is None and self.max_records is None

    def cutoff_timestamp(self, now: float | None = None) -> float | None:
        if self.max_age_days is None:
            return None
        reference = now if now is not None else time.time()
        return reference - (self.max_age_days * 86400.0)

    def count_threshold(self) -> int | None:
        """Line count above which the count cap prunes; ``None`` without a count cap."""
        if self.max_records is None:
            return None
        return self.max_records + max(1, int(self.max_records * _COUNT_SLACK_FRACTION))


@dataclass(frozen=True)
class RewriteResult:
    """Outcome of one locked pass.

    ``pruned`` counts records removed by policy. ``torn`` counts a dropped unterminated
    final line, which is not a record. ``head`` is the chain head after the pass, or
    ``None`` when the pass changed nothing.
    """

    pruned: int
    torn: int
    head: str | None

    @property
    def removed(self) -> int:
        return self.pruned + self.torn


@dataclass(frozen=True)
class _Plan:
    """Result of pass 1: record and torn counts, the leading records to drop, and the last record's hash."""

    valid: int
    torn: int
    drop: int
    last_hash: str | None


def apply_retention_to_jsonl(
    path: str | os.PathLike[str],
    policy: RetentionPolicy,
    *,
    now: float | None = None,
) -> int:
    """Prune the ledger at ``path`` in place and return the number of lines removed.

    The age cap removes only a contiguous run of the oldest records. The count cap then
    trims the oldest records. The chain is verified before any removal. A line that
    fails verification raises :class:`LedgerIntegrityError`, and the ledger and its
    anchor are left untouched. A torn final line (no trailing newline and not parseable)
    is dropped, and forces a full pass.

    The pass holds the ``.lock`` file, so appends are not lost. A ledger that is a
    symlink or not a regular file raises ``OSError``.
    """
    if policy.is_noop:
        return 0

    src = Path(path)
    with locked(sibling(src, ".lock")):
        return rewrite_jsonl_locked(src, policy, now=now).removed


def verify_ledger(path: str | os.PathLike[str]) -> None:
    """Verify the chain on disk with the checks retention applies.

    Starts from the anchor when one exists. Raises :class:`LedgerIntegrityError` on the
    first problem. A torn final line is tolerated.
    """
    src = Path(path)
    earlier = read_anchor(src)
    try:
        fh = open_ledger_file(src, os.O_RDONLY, "rb")
    except FileNotFoundError:
        return
    with fh:
        # Drain the scan: consuming it runs every ledger check.
        deque(_scan(fh, accepted_links(earlier), allow_legacy=_legacy_allowed(earlier)), maxlen=0)


def rewrite_jsonl_locked(
    path: str | os.PathLike[str],
    policy: RetentionPolicy,
    *,
    now: float | None = None,
) -> RewriteResult:
    """Apply ``policy`` to ``path``. The caller already holds the ``.lock`` file.

    Pass 1 (:func:`_plan_prune`) verifies the chain and counts what to remove. When
    nothing is removed, the pass ends there. Pass 2 (:func:`_write_survivors`) streams
    the survivors into a tmp file, verifies them again on the same descriptor, commits
    the anchor, and then replaces the ledger.

    The anchor is committed before the ledger is replaced. A crash after the anchor
    leaves the old ledger, whose first record links to the anchor's ``previous_hash``.
    A crash after the replace leaves the new ledger, which links to the anchor's
    ``hash``. Both states verify.
    """
    src = Path(path)
    tmp = sibling(src, ".tmp")
    tmp.unlink(missing_ok=True)
    sibling(anchor_path(src), ".tmp").unlink(missing_ok=True)

    cutoff = policy.cutoff_timestamp(now=now)
    try:
        fh = open_ledger_file(src, os.O_RDONLY, "rb")
    except FileNotFoundError:
        return RewriteResult(0, 0, None)
    with fh:
        earlier = read_anchor(src)
        if not _may_remove(fh, policy, cutoff):
            return RewriteResult(0, 0, None)
        accepted = accepted_links(earlier)
        legacy = _legacy_allowed(earlier)
        fh.seek(0)
        plan = _plan_prune(fh, policy, cutoff, accepted, legacy)
        if plan.drop + plan.torn == 0:
            return RewriteResult(0, 0, None)
        fh.seek(0)
        return _write_survivors(fh, plan, src, tmp, accepted, legacy, earlier, now)


def _latest_hash(current: str | None, new: str | None) -> str | None:
    """``new`` when it is set, otherwise ``current``: the most recent hash seen."""
    return new if new is not None else current


def _advance_age_search(
    age: float | None,
    cutoff: float,
    valid: int,
    pending: int | None,
) -> tuple[int | None, int | None]:
    """One step of the age search, returning ``(prefix, pending)``. ``prefix`` is set once a known age reaches the cutoff."""
    if age is None:
        return None, (valid if pending is None else pending)
    if age >= cutoff:
        return (valid if pending is None else pending), None
    return None, None


def _count_drop(valid: int, drop: int, policy: RetentionPolicy) -> int:
    """Raise the drop to what the count cap requires, once the ledger is past its slack threshold."""
    threshold = policy.count_threshold()
    if threshold is not None and valid > threshold:
        return max(drop, valid - policy.max_records)
    return drop


def _plan_prune(
    fh: IO[bytes],
    policy: RetentionPolicy,
    cutoff: float | None,
    accepted: tuple[str, ...],
    legacy: bool,
) -> _Plan:
    """Pass 1: verify every line and count the leading records the policy removes.

    The age search stops at the first record whose known age reaches the cutoff. Lines
    with an unknown age wait for the next known age, and are removed or kept with it.
    """
    valid = 0
    torn = 0
    prefix: int | None = 0 if cutoff is None else None
    pending: int | None = None
    last_hash: str | None = None
    for _, record, age in _scan(fh, accepted, allow_legacy=legacy):
        if record is None:
            torn += 1
            continue
        if cutoff is not None and prefix is None:
            prefix, pending = _advance_age_search(age, cutoff, valid, pending)
        valid += 1
        last_hash = _latest_hash(last_hash, record.hash)
    if prefix is None:
        prefix = valid if pending is None else pending
    drop = _count_drop(valid, prefix, policy)
    return _Plan(valid=valid, torn=torn, drop=drop, last_hash=last_hash)


@dataclass
class _Survivors:
    """State of pass 2: what was written to the tmp file and the chain links it carries."""

    drop: int
    out: IO[bytes]
    index: int = 0
    first_prev: str | None = None
    first_legacy: bool = False
    head: str | None = None
    last_hash: str | None = None
    kept_last: str | None = None
    kept_first_legacy: bool = False

    def feed(self, line: bytes, record: AuditRecord) -> None:
        """Account for one verified record and write it when it is kept."""
        if self.index == 0:
            self.first_prev = record.prev_hash
            self.first_legacy = record.prev_hash is None
        if self.index == self.drop:
            self.head = record.prev_hash if record.prev_hash is not None else GENESIS_PREV_HASH
            self.kept_first_legacy = record.prev_hash is None
        if self.index >= self.drop:
            self.out.write(line + b"\n")
            self.kept_last = _latest_hash(self.kept_last, record.hash)
        self.last_hash = _latest_hash(self.last_hash, record.hash)
        self.index += 1


def _copy_survivors(
    fh: IO[bytes],
    plan: _Plan,
    accepted: tuple[str, ...],
    legacy: bool,
    out: IO[bytes],
) -> _Survivors:
    """Pass 2 read: stream every verified record through ``_Survivors.feed`` into ``out``."""
    survivors = _Survivors(drop=plan.drop, out=out)
    for line, record, _age in _scan(fh, accepted, allow_legacy=legacy):
        if record is not None:
            survivors.feed(line, record)
    return survivors


def _chain_links(plan: _Plan, survivors: _Survivors, fallback: str) -> tuple[str | None, str | None]:
    """The ``(hash, previous_hash)`` pair the anchor records for this pass."""
    if plan.valid == 0:
        return fallback, fallback
    previous = fallback if survivors.first_legacy else survivors.first_prev
    head = survivors.head
    if plan.drop == plan.valid:
        head = survivors.last_hash if survivors.last_hash is not None else fallback
    return head, previous


def _write_survivors(
    fh: IO[bytes],
    plan: _Plan,
    src: Path,
    tmp: Path,
    accepted: tuple[str, ...],
    legacy: bool,
    earlier: RetentionAnchor | None,
    now: float | None,
) -> RewriteResult:
    """Pass 2: stream the records to keep into ``tmp``, verifying as it goes, then commit."""
    fallback = earlier.hash if earlier is not None else GENESIS_PREV_HASH
    fd = create_tmp(tmp)
    try:
        with os.fdopen(fd, "wb") as wh:
            survivors = _copy_survivors(fh, plan, accepted, legacy, wh)
            if survivors.index != plan.valid or survivors.last_hash != plan.last_hash:
                raise LedgerIntegrityError(
                    "ledger changed during retention; aborting, retention halted until the ledger is repaired"
                )
            wh.flush()
            os.fsync(wh.fileno())
        head, previous = _chain_links(plan, survivors, fallback)
        write_anchor(
            src,
            new_anchor(
                hash=head,
                previous_hash=previous,
                removed=plan.drop,
                earlier=earlier,
                now=now,
                legacy_prefix=survivors.kept_first_legacy,
            ),
        )
        os.replace(tmp, src)
        fsync_dir(src)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return RewriteResult(plan.drop, plan.torn, _latest_hash(head, survivors.kept_last))


def oldest_age_reference(path: str | os.PathLike[str]) -> float | None:
    """Age of the first record with a known age, or ``None`` when there is none to read."""
    try:
        fh = open_ledger_file(Path(path), os.O_RDONLY, "rb")
    except FileNotFoundError:
        return None
    with fh:
        for _, _, line in nonblank(fh):
            try:
                payload, _record = parse_record(line.decode("utf-8"))
                age = known_age_reference(payload)
            except ValueError:
                return None
            if age is not None:
                return age
    return None


def pin_ahead_seconds(path: str | os.PathLike[str], now: float) -> float | None:
    """Seconds the oldest record with a known age is ahead of ``now``, when more than ``FUTURE_PIN_SECONDS``.

    Such a record is never older than the cutoff, so it stops the contiguous run and
    pins every record after it. Records with an unknown age do not pin. They take
    their successor's age. ``None`` means the ledger is not pinned.
    """
    reference = oldest_age_reference(path)
    if reference is None:
        return None
    ahead = reference - now
    return ahead if ahead > FUTURE_PIN_SECONDS else None


def _legacy_allowed(earlier: RetentionAnchor | None) -> bool:
    """Hash-less lines are accepted only when there is no anchor, or the anchor flags a legacy prefix."""
    return earlier is None or earlier.legacy_prefix


def _has_torn_tail(fh: IO[bytes], size: int) -> bool:
    """True when the last byte of the ledger is not a newline, so the final line is unterminated."""
    fh.seek(size - 1)
    return fh.read(1) != b"\n"


def _first_known_age_before(fh: IO[bytes], cutoff: float) -> bool:
    """True when the first record with a known age cannot be read, or is not finite or is older than ``cutoff``."""
    for _, _, line in nonblank(fh):
        try:
            payload, _record = parse_record(line.decode("utf-8"))
            age = known_age_reference(payload)
        except ValueError:
            return True
        if age is None:
            continue
        # A non-finite age cannot be compared, so the full pass must refuse it.
        return not math.isfinite(age) or age < cutoff
    return False


def _over_count_threshold(fh: IO[bytes], policy: RetentionPolicy) -> bool:
    """True when the newline count, an upper bound on the records, exceeds the count threshold."""
    threshold = policy.count_threshold()
    if threshold is None:
        return False
    fh.seek(0)
    lines = sum(block.count(b"\n") for block in iter(lambda: fh.read(_READ_BLOCK), b""))
    return lines > threshold


def _may_remove(fh: IO[bytes], policy: RetentionPolicy, cutoff: float | None) -> bool:
    """Cheap check: can the policy remove anything, or must the full pass verify first?

    A torn tail always forces the full pass. For age, the first record with a known age
    decides, and an unparseable line before it also forces the full pass. For count, the
    newline count is an upper bound on the records, so a ledger under the threshold is
    skipped.
    """
    fh.seek(0, os.SEEK_END)
    size = fh.tell()
    if size == 0:
        return False
    if _has_torn_tail(fh, size):
        return True
    fh.seek(0)
    if cutoff is not None and _first_known_age_before(fh, cutoff):
        return True
    return _over_count_threshold(fh, policy)


def _check_fields(payload: dict[str, Any]) -> None:
    if set(payload) - _RECORD_FIELDS:
        raise ValueError("unknown field")


def _cannot_serialise(number: int, exc: Exception) -> LedgerIntegrityError:
    return LedgerIntegrityError(
        f"ledger line {number} cannot be serialised ({type(exc).__name__}); "
        "retention halted until the ledger is repaired"
    )


def validate_chained(
    line: bytes,
    number: int,
    payload: dict[str, Any],
    record: AuditRecord,
    linked_to: tuple[str, ...],
) -> None:
    """Check a chained line: canonical form, then link, then hash.

    The scan and the torn-fragment repair both call this, so a fragment is never
    restored under weaker rules than a line already in the ledger. Errors name the line
    number and the record id, never the content.
    """
    data = record.to_dict()
    try:
        serialised = json.dumps(data, separators=(",", ":"), allow_nan=False)
    except ValueError:
        if "received_at" in payload:
            raise LedgerIntegrityError(
                f"ledger line {number} holds a non-finite value but carries received_at; "
                "retention halted until the ledger is repaired"
            ) from None
        # A non-finite number is written as a NaN or Infinity literal, and the line's hash
        # was computed over that literal. Compare with the same serialiser.
        try:
            serialised = json.dumps(data, separators=(",", ":"))
        except (ValueError, TypeError, RecursionError) as exc:
            raise _cannot_serialise(number, exc) from None
    except (TypeError, RecursionError) as exc:
        raise _cannot_serialise(number, exc) from None
    if line != serialised.encode("utf-8"):
        raise LedgerIntegrityError(
            f"ledger line {number} is not in canonical form; retention halted until the ledger is repaired"
        )
    if record.prev_hash not in linked_to:
        raise LedgerIntegrityError(
            f"ledger chain broken at line {number} (record {short_id(record.id)}): prev_hash mismatch; "
            "retention halted until the ledger is repaired"
        )
    if record.hash != hash_of_dict(data):
        raise LedgerIntegrityError(
            f"ledger chain broken at line {number} (record {short_id(record.id)}): hash mismatch; "
            "retention halted until the ledger is repaired"
        )


def _parse_ledger_line(line: bytes) -> tuple[dict[str, Any], AuditRecord, float | None]:
    """Decode and field-check one ledger line, returning its payload, record and known age."""
    payload, record = parse_record(line.decode("utf-8"))
    _check_fields(payload)
    if record.received_at is not None and not math.isfinite(record.received_at):
        raise ValueError("non-finite received_at")
    return payload, record, known_age_reference(payload)


def _is_legacy_line(
    payload: dict[str, Any],
    number: int,
    record: AuditRecord,
    allow_legacy: bool,
    seen_chained: bool,
) -> bool:
    """Whether a hash-less line is an accepted legacy prefix line. A hash-less line carrying ``received_at`` is refused."""
    if record.prev_hash is not None or record.hash is not None:
        return False
    if payload.get("received_at") is not None:
        raise LedgerIntegrityError(
            f"ledger line {number} is hash-less but carries received_at; "
            "retention halted until the ledger is repaired"
        )
    return allow_legacy and not seen_chained


def _scan(
    fh: IO[bytes],
    accepted: tuple[str, ...],
    *,
    allow_legacy: bool,
) -> Iterator[tuple[bytes, AuditRecord | None, float | None]]:
    """Yield ``(raw_line, record, known_age)`` for each line of the ledger.

    ``record`` is ``None`` for a dropped torn tail. ``known_age`` is ``None`` when the
    line has no usable age. This is the one set of ledger checks, used by pruning and by
    verification. Lines are decoded one at a time. Duplicate keys, unknown fields and a
    non-finite ``received_at`` are rejected. The first chained record must link to one
    of ``accepted``, and each later one to its predecessor. Each hash must match its
    content. With ``allow_legacy``, hash-less lines are accepted until the first chained
    line, and rejected after it. Errors name the line number and, for a chain break, the
    record id. They never include the content.
    """
    previous_hash: str | None = None
    seen_chained = False
    for number, raw, line in nonblank(fh):
        try:
            payload, record, age = _parse_ledger_line(line)
        except (ValueError, TypeError, AttributeError):
            if not raw.endswith(b"\n"):
                yield line, None, None
                continue
            raise LedgerIntegrityError(
                f"ledger line {number} is malformed; retention halted until the ledger is repaired"
            ) from None
        if _is_legacy_line(payload, number, record, allow_legacy, seen_chained):
            yield line, record, age
            continue
        if record.prev_hash is None:
            raise LedgerIntegrityError(
                f"ledger line {number} lacks prev_hash; retention halted until the ledger is repaired"
            )
        linked_to = accepted if previous_hash is None else (previous_hash,)
        validate_chained(line, number, payload, record, linked_to)
        seen_chained = True
        previous_hash = record.hash
        yield line, record, age
