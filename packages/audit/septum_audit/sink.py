"""Pluggable persistence backends for :class:`AuditRecord`."""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
import threading
from pathlib import Path
from typing import Iterable, Iterator, Protocol, Sequence, runtime_checkable

from .events import GENESIS_PREV_HASH, AuditRecord


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


@contextlib.contextmanager
def _flock(path: Path):
    """Acquire an exclusive POSIX advisory lock for the duration of a block.

    Used to serialise appenders (and the retention rewriter) across
    processes; ``asyncio.Lock`` only serialises within one process and
    operators can run multiple workers behind the same sink path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o640)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield fd
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


class JsonlFileSink:
    """Append-only newline-delimited JSON sink with hash-chain integrity.

    Concurrent writers from different processes are serialised by an
    advisory ``fcntl.flock`` on a sibling lockfile (``.lock``). Earlier
    versions relied on POSIX atomic-append semantics, which only hold
    below ``PIPE_BUF`` for pipes — large records mid-line could
    interleave on regular files. Per-line open-close is intentional so
    logrotate keeps working without a SIGHUP dance.

    Each record is stamped with ``prev_hash`` (hash of the previous
    record on disk, or 64 zeros for the genesis entry) and ``hash``
    (sha256 over the record's canonical JSON minus the ``hash`` field).
    Operators can tail the file through :func:`verify_chain` to detect
    any post-write tampering.

    Mode bits are constrained: parent directories are created 0o750
    and the JSONL itself is created 0o640 so a passing local user
    cannot read the audit ledger by default.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        self._lockfile = self._path.with_suffix(self._path.suffix + ".lock")
        self._lock = asyncio.Lock()
        self._cached_last_hash: str | None = None
        self._cached_last_hash_state = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    async def write(self, record: AuditRecord) -> None:
        async with self._lock:
            await asyncio.to_thread(self._append, record)

    def _last_hash(self) -> str:
        """Return the most recent on-disk record hash or the genesis."""
        with self._cached_last_hash_state:
            if self._cached_last_hash is not None:
                return self._cached_last_hash
        last = GENESIS_PREV_HASH
        try:
            fh = self._path.open("r", encoding="utf-8")
        except FileNotFoundError:
            with self._cached_last_hash_state:
                self._cached_last_hash = last
            return last
        with fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                candidate = payload.get("hash")
                if candidate:
                    last = str(candidate)
        with self._cached_last_hash_state:
            self._cached_last_hash = last
        return last

    def _append(self, record: AuditRecord) -> None:
        with _flock(self._lockfile):
            prev_hash = self._last_hash()
            chained = record.with_hash_chain(prev_hash=prev_hash)
            line = chained.to_json()
            # Open the audit file with explicit 0o640 so a fresh sink
            # never inherits a wider umask. ``os.open`` short-circuits
            # the rewrite on subsequent appends since the mode arg is
            # only used at create time.
            fd = os.open(
                str(self._path),
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o640,
            )
            try:
                with os.fdopen(fd, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
            except Exception:
                os.close(fd)
                raise
            with self._cached_last_hash_state:
                self._cached_last_hash = chained.hash

    def read_all(self) -> Iterator[AuditRecord]:
        try:
            fh = self._path.open("r", encoding="utf-8")
        except FileNotFoundError:
            return iter(())
        return self._iter_records(fh)

    @staticmethod
    def _iter_records(fh) -> Iterator[AuditRecord]:
        with fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    # Truncated tail of an actively-written log: skip.
                    continue
                yield AuditRecord.from_dict(payload)

    async def close(self) -> None:
        return None
