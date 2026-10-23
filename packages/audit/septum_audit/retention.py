"""Age- and count-based retention for jsonl sinks."""

from __future__ import annotations

import dataclasses
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Iterator

from .anchor import RetentionAnchor, accepted_links, anchor_path, new_anchor, read_anchor, write_anchor
from .events import (
    GENESIS_PREV_HASH,
    AuditRecord,
    LedgerIntegrityError,
    hash_of_dict,
    known_age_reference,
    parse_record,
    short_id,
)
from .fsutil import nonblank, create_tmp, fsync_dir, locked, open_ledger_file, sibling

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
        for _ in _scan(fh, accepted_links(earlier), allow_legacy=_legacy_allowed(earlier)):
            pass


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
            if age is None:
                if pending is None:
                    pending = valid
            else:
                if age >= cutoff:
                    prefix = pending if pending is not None else valid
                pending = None
        valid += 1
        if record.hash is not None:
            last_hash = record.hash
    if prefix is None:
        prefix = pending if pending is not None else valid
    drop = prefix
    threshold = policy.count_threshold()
    if threshold is not None and valid > threshold:
        drop = max(drop, valid - policy.max_records)
    return _Plan(valid=valid, torn=torn, drop=drop, last_hash=last_hash)


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
            index = 0
            first_prev: str | None = None
            first_legacy = False
            head: str | None = None
            last_hash: str | None = None
            kept_last: str | None = None
            kept_first_legacy = False
            for line, record, _age in _scan(fh, accepted, allow_legacy=legacy):
                if record is None:
                    continue
                if index == 0:
                    first_prev = record.prev_hash
                    first_legacy = record.prev_hash is None
                if index == plan.drop:
                    head = record.prev_hash if record.prev_hash is not None else GENESIS_PREV_HASH
                    kept_first_legacy = record.prev_hash is None
                if index >= plan.drop:
                    wh.write(line + b"\n")
                    if record.hash is not None:
                        kept_last = record.hash
                if record.hash is not None:
                    last_hash = record.hash
                index += 1
            if index != plan.valid or last_hash != plan.last_hash:
                raise LedgerIntegrityError(
                    "ledger changed during retention; aborting, retention halted until the ledger is repaired"
                )
            wh.flush()
            os.fsync(wh.fileno())
        if plan.valid == 0:
            head = previous = fallback
        else:
            previous = first_prev if not first_legacy else fallback
            if plan.drop == plan.valid:
                head = last_hash if last_hash is not None else fallback
        new_head = kept_last if kept_last is not None else head
        write_anchor(
            src,
            new_anchor(
                hash=head,
                previous_hash=previous,
                removed=plan.drop,
                earlier=earlier,
                now=now,
                legacy_prefix=kept_first_legacy,
            ),
        )
        os.replace(tmp, src)
        fsync_dir(src)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return RewriteResult(plan.drop, plan.torn, new_head)


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
    fh.seek(size - 1)
    if fh.read(1) != b"\n":
        return True
    fh.seek(0)
    if cutoff is not None:
        for _, _, line in nonblank(fh):
            try:
                payload, _record = parse_record(line.decode("utf-8"))
                age = known_age_reference(payload)
            except ValueError:
                return True
            if age is None:
                continue
            # A non-finite age cannot be compared, so the full pass must refuse it.
            if not math.isfinite(age) or age < cutoff:
                return True
            break
    threshold = policy.count_threshold()
    if threshold is not None:
        fh.seek(0)
        lines = 0
        while True:
            block = fh.read(_READ_BLOCK)
            if not block:
                break
            lines += block.count(b"\n")
        if lines > threshold:
            return True
    return False


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
            payload, record = parse_record(line.decode("utf-8"))
            _check_fields(payload)
            if record.received_at is not None and not math.isfinite(record.received_at):
                raise ValueError("non-finite received_at")
            age = known_age_reference(payload)
        except (ValueError, TypeError, AttributeError):
            if not raw.endswith(b"\n"):
                yield line, None, None
                continue
            raise LedgerIntegrityError(
                f"ledger line {number} is malformed; retention halted until the ledger is repaired"
            ) from None
        if record.prev_hash is None and record.hash is None:
            if payload.get("received_at") is not None:
                raise LedgerIntegrityError(
                    f"ledger line {number} is hash-less but carries received_at; "
                    "retention halted until the ledger is repaired"
                )
            if allow_legacy and not seen_chained:
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
