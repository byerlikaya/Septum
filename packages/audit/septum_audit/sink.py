"""Pluggable persistence backends for :class:`AuditRecord`."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import math
import os
import time
from pathlib import Path
from typing import Callable, Iterable, Iterator, Protocol, Sequence, runtime_checkable

from .anchor import accepted_links, is_hash, read_anchor
from .events import (
    GENESIS_PREV_HASH,
    INGEST_FLAG,
    INGEST_UNSTORABLE,
    AuditRecord,
    UnstorablePlaceholder,
    UnstorableRecordError,
    normalise_attributes,
    parse_line,
    parse_record,
    short_id,
)
from .fsutil import locked, nonblank, open_ledger_fd, open_ledger_file, sibling
from .retention import (
    RetentionPolicy,
    RewriteResult,
    rewrite_jsonl_locked,
    validate_chained,
    verify_ledger,
)

logger = logging.getLogger(__name__)
_TAIL_BLOCK = 4096
# A torn fragment larger than this is truncated without being read or parsed.
_TORN_PARSE_LIMIT = 1 << 20


@runtime_checkable
class AuditSink(Protocol):
    """Storage contract every audit backend implements."""

    async def write(self, record: AuditRecord) -> None: ...

    def read_all(self) -> Iterable[AuditRecord]: ...

    async def close(self) -> None: ...


class MemorySink:
    """In-process list sink. Safe under asyncio; not thread-safe."""

    def __init__(self, initial_records: Sequence[AuditRecord] | None = None) -> None:
        self._records: list[AuditRecord] = []
        prev = GENESIS_PREV_HASH
        for record in initial_records or ():
            chained = record.with_hash_chain(prev_hash=prev)
            self._records.append(chained)
            prev = chained.hash or prev

    async def write(self, record: AuditRecord) -> None:
        prev = self._records[-1].hash if self._records else GENESIS_PREV_HASH
        self._records.append(record.with_hash_chain(prev_hash=prev or GENESIS_PREV_HASH))

    def read_all(self) -> Iterator[AuditRecord]:
        # Snapshot so an iterator does not see records appear mid-iteration.
        return iter(list(self._records))

    def __len__(self) -> int:
        return len(self._records)

    async def close(self) -> None:
        return None


class JsonlFileSink:
    """Append-only newline-delimited JSON sink with hash-chain integrity.

    Appends and retention passes are serialised by an advisory lock on a sibling
    ``.lock`` file, across threads and processes, and by the sink's asyncio lock
    within a process. Each record is stamped with ``received_at`` (the sink's
    clock at ingest), ``prev_hash`` (the hash of the previous record on disk, or
    genesis) and ``hash`` (sha256 over the canonical JSON minus ``hash``). A failed
    append is rolled back to the size it started from, and the cached chain head
    is dropped so the next append re-reads the ledger.

    The ledger and its sidecars are opened without following symlinks and must be
    regular files. Files are created with mode 0o640 and directories with 0o750.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = Path(path)
        self._clock = clock
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        self._lockfile = sibling(self._path, ".lock")
        self._lock = asyncio.Lock()
        # The cached chain head is trusted only while the ledger's (inode, size) still
        # matches the values recorded with it. Any other writer changes one of them.
        self._cached_last_hash: str | None = None
        self._cached_stamp: tuple[int, int] | None = None

    @property
    def path(self) -> Path:
        return self._path

    async def write(self, record: AuditRecord) -> None:
        """Stamp and append one record. Its content is normalised, never dropped.

        ``attributes`` is normalised to the shape it reads back as (see
        :func:`normalise_attributes`). A timestamp that is not a finite, non-zero number
        is replaced by the ingest time, and the original is not kept. Each replacement
        sets the hash-covered ``INGEST_FLAG``, which says only that a value in this
        record was replaced, not which field. A producer copy of that flag is removed
        first. A line that would not verify when read back raises
        :class:`UnstorableRecordError` and is not written.
        """
        now = self._clock()
        attributes, changed = normalise_attributes(record.attributes)
        attributes.pop(INGEST_FLAG, None)
        attributes.pop(INGEST_UNSTORABLE, None)
        if isinstance(record, UnstorablePlaceholder):
            attributes[INGEST_UNSTORABLE] = True
        timestamp = record.timestamp
        if not math.isfinite(timestamp) or timestamp == 0:
            timestamp = now
            changed = True
        if changed:
            attributes[INGEST_FLAG] = True
        stamped = dataclasses.replace(
            record,
            timestamp=timestamp,
            attributes=attributes,
            received_at=now,
        )
        async with self._lock:
            await asyncio.to_thread(self._append, stamped)

    async def apply_retention(
        self,
        policy: RetentionPolicy,
        *,
        now: float | None = None,
    ) -> int:
        """Run one pass and return the number of lines it removed, pruned and torn together."""
        return (await self.apply_retention_outcome(policy, now=now)).removed

    async def apply_retention_outcome(
        self,
        policy: RetentionPolicy,
        *,
        now: float | None = None,
    ) -> RewriteResult:
        """Run one pass and report pruned records and the torn fragment separately.

        The pass holds the sink's asyncio lock and the ``.lock`` file, so no append
        lands between the read and the replace.
        """
        if policy.is_noop:
            return RewriteResult(0, 0, None)
        async with self._lock:
            return await asyncio.to_thread(self._retain, policy, now)

    def _retain(self, policy: RetentionPolicy, now: float | None) -> RewriteResult:
        with locked(self._lockfile):
            result = rewrite_jsonl_locked(self._path, policy, now=now)
            if result.removed:
                self._set_cache(result.head)
        return result

    def _ledger_stamp(self) -> tuple[int, int] | None:
        try:
            info = os.stat(self._path, follow_symlinks=False)
        except FileNotFoundError:
            return None
        return (info.st_ino, info.st_size)

    def _set_cache(self, head: str) -> None:
        self._cached_last_hash = head
        self._cached_stamp = self._ledger_stamp()

    def _clear_cache(self) -> None:
        self._cached_last_hash = None
        self._cached_stamp = None

    def verify(self) -> None:
        """Verify the on-disk chain with the same checks retention applies.

        Starts from the retention anchor when a prune happened. Raises ``ValueError``
        (a :class:`LedgerIntegrityError`) on the first problem.
        """
        verify_ledger(self._path)

    def _last_hash(self) -> str:
        """Return the chain head the next record must link to.

        Only the tail of the file is read on a cold start. With no readable record
        (never written, or fully pruned), the chain continues from the anchor, or
        from genesis when no prune has happened.
        """
        if self._cached_last_hash is not None and self._cached_stamp == self._ledger_stamp():
            return self._cached_last_hash
        last: str | None = None
        try:
            fh = open_ledger_file(self._path, os.O_RDONLY, "rb")
        except FileNotFoundError:
            fh = None
        if fh is not None:
            with fh:
                last = _tail_hash(fh)
        if last is None:
            anchor = read_anchor(self._path)
            last = anchor.hash if anchor is not None else GENESIS_PREV_HASH
        self._set_cache(last)
        return last

    def _drop_torn_tail(self) -> None:
        """Repair an unterminated final line left by a crash mid-write.

        A fragment that passes every check a complete line must pass, and links to its
        predecessor, is a whole record whose newline was lost: the newline is restored.
        Anything else was never acknowledged (the consumer acknowledges after the append
        returns, so the message is redelivered), so it is truncated. Either way the
        cached head is cleared.
        """
        try:
            fh = open_ledger_file(self._path, os.O_RDWR, "r+b")
        except FileNotFoundError:
            return
        with fh:
            size = fh.seek(0, os.SEEK_END)
            if size == 0:
                return
            fh.seek(size - 1)
            if fh.read(1) == b"\n":
                return
            fragment_start = _last_newline_before(fh, size) + 1
            restore = False
            if size - fragment_start <= _TORN_PARSE_LIMIT:
                fh.seek(fragment_start)
                fragment = fh.read(size - fragment_start)
                predecessor = _predecessor_hash(fh, fragment_start)
                options = (predecessor,) if predecessor is not None else accepted_links(read_anchor(self._path))
                restore = _fragment_chains(fragment, options)
            if restore:
                fh.seek(size)
                fh.write(b"\n")
                action = "restored the missing newline on"
            else:
                fh.truncate(fragment_start)
                action = "dropped"
            fh.flush()
            os.fsync(fh.fileno())
        self._clear_cache()
        logger.warning("audit ledger: %s an unterminated final line (crash residue)", action)

    def _append(self, record: AuditRecord) -> None:
        with locked(self._lockfile):
            self._drop_torn_tail()
            prev_hash = self._last_hash()
            chained = record.with_hash_chain(prev_hash=prev_hash)
            line = chained.to_json().encode("utf-8")
            _check_reads_back(line, prev_hash, chained.id)
            data = line + b"\n"
            try:
                # Mode 0o640 applies only when the file is created.
                fd = open_ledger_fd(self._path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
                size = None
                try:
                    size = os.fstat(fd).st_size
                    _write_all(fd, data)
                    os.fsync(fd)
                except BaseException:
                    if size is not None:
                        _rollback(fd, size)
                    raise
                finally:
                    os.close(fd)
            except BaseException:
                # Any failure, including close() after a durable write, leaves the
                # cache unknown: the next append re-reads the ledger.
                self._clear_cache()
                raise
            self._set_cache(chained.hash)

    def read_all(self) -> Iterator[AuditRecord]:
        try:
            fh = open_ledger_file(self._path, os.O_RDONLY, "rb")
        except FileNotFoundError:
            return iter(())
        return self._iter_records(fh)

    @staticmethod
    def _iter_records(fh) -> Iterator[AuditRecord]:
        with fh:
            for _, _, line in nonblank(fh):
                try:
                    _, record = parse_record(line.decode("utf-8"))
                except ValueError:
                    # Corrupt or truncated line: skipped here; verify() reports the break.
                    continue
                yield record

    async def close(self) -> None:
        return None


def _tail_hash(fh) -> str | None:
    """Chain hash of the last line of the file that carries a valid one."""
    return _predecessor_hash(fh, fh.seek(0, os.SEEK_END))


def _predecessor_hash(fh, end: int) -> str | None:
    """Chain hash of the last line before ``end`` that carries a valid one.

    Walks backwards by block, skipping blank lines, unparseable lines and lines
    without a hash, so only the tail of the file is read in the common case.
    """
    while end > 0:
        newline = _last_newline_before(fh, end)
        start = newline + 1
        fh.seek(start)
        raw = fh.read(end - start).strip()
        if raw:
            found = _line_hash(raw)
            if found is not None:
                return found
        if newline == -1:
            break
        end = newline
    return None


def _check_reads_back(line: bytes, prev_hash: str, record_id: str) -> None:
    """Refuse to write a line that would not verify when it is read back."""
    try:
        payload, parsed = parse_record(line.decode("utf-8"))
        validate_chained(line, 0, payload, parsed, (prev_hash,))
    except (ValueError, TypeError) as exc:
        raise UnstorableRecordError(
            f"record {short_id(record_id)} would not verify on read-back: {type(exc).__name__}"
        ) from None


def _line_hash(raw: bytes) -> str | None:
    try:
        payload = parse_line(raw.decode("utf-8"))
    except ValueError:
        return None
    value = payload.get("hash")
    return value if is_hash(value) else None


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def _rollback(fd: int, size: int) -> None:
    """Cut the ledger back to ``size`` after a failed append. The caller re-raises the original error."""
    try:
        os.ftruncate(fd, size)
        os.fsync(fd)
    except OSError:
        logger.error(
            "audit ledger: rollback after a failed append did not complete; the next append re-reads the ledger"
        )


def _last_newline_before(fh, end: int) -> int:
    """Index of the last newline in ``[0, end)``, or -1."""
    pos = end
    while pos > 0:
        start = max(0, pos - _TAIL_BLOCK)
        fh.seek(start)
        index = fh.read(pos - start).rfind(b"\n")
        if index != -1:
            return start + index
        pos = start
    return -1


def _fragment_chains(fragment: bytes, options: tuple[str, ...]) -> bool:
    """True when an unterminated line passes every check a complete chained line must pass.

    The checks are the scan's own (``validate_chained``): canonical form (which also
    rejects unknown fields), the link, and the hash.
    """
    text = fragment.strip()
    try:
        payload, record = parse_record(text.decode("utf-8"))
        validate_chained(text, 0, payload, record, options)
    except ValueError:
        return False
    return True
