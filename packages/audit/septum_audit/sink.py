"""Pluggable persistence backends for :class:`AuditRecord`.

A sink is the storage primitive every producer (queue consumer, FastAPI
endpoint, direct API call) writes through. Two are bundled:

* :class:`JsonlFileSink` — append-only newline-delimited JSON. Atomic
  per-line writes, safe for concurrent processes via ``O_APPEND``. The
  default in production: file-system tools (``logrotate``, ``tail``,
  rsync) just work.
* :class:`MemorySink` — in-process list. Used by tests and the
  embedded API process when the operator only wants ephemeral counts.

The :class:`AuditSink` Protocol keeps the surface narrow so a future
backend (S3, Postgres, Loki) drops in without touching the consumer or
the exporters.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import AsyncIterator, Iterable, Iterator, Protocol, runtime_checkable

from .events import AuditRecord


@runtime_checkable
class AuditSink(Protocol):
    """Storage contract every audit backend implements.

    Implementations may be sync, async, or wrap blocking I/O in a
    threadpool. ``write`` is awaitable so the consumer loop never has
    to branch on backend type. ``read_all`` is the exporter entry point
    — it streams every record so a CSV/JSON dump never builds the
    full list in memory.
    """

    async def write(self, record: AuditRecord) -> None: ...

    def read_all(self) -> Iterable[AuditRecord]: ...

    async def close(self) -> None: ...


class MemorySink:
    """In-process list sink. Safe under asyncio; not thread-safe."""

    def __init__(self) -> None:
        self._records: list[AuditRecord] = []

    async def write(self, record: AuditRecord) -> None:
        self._records.append(record)

    def read_all(self) -> Iterator[AuditRecord]:
        # Yield a snapshot so a caller iterating during concurrent
        # writes does not see records appear mid-iteration.
        return iter(list(self._records))

    def __len__(self) -> int:
        return len(self._records)

    async def close(self) -> None:
        # Nothing to release; kept to satisfy the protocol.
        return None


class JsonlFileSink:
    """Append-only newline-delimited JSON sink.

    Each record becomes one line. Concurrent writers from different
    processes are safe because POSIX guarantees atomic appends below
    ``PIPE_BUF`` (4 KiB on Linux); audit lines are well under that
    limit. Larger payloads would still serialize but the OS may
    interleave bytes — we deliberately keep ``attributes`` small.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._path

    async def write(self, record: AuditRecord) -> None:
        # Run the blocking write under to_thread so the async consumer
        # loop is never stalled by a slow filesystem.
        line = record.to_json()
        async with self._lock:
            await asyncio.to_thread(self._append, line)

    def _append(self, line: str) -> None:
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def read_all(self) -> Iterator[AuditRecord]:
        if not self._path.exists():
            return iter(())
        return self._iter_records()

    def _iter_records(self) -> Iterator[AuditRecord]:
        with self._path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    # A truncated line at the tail of an actively-written
                    # log is more useful skipped than fatal — the next
                    # read picks up the rest.
                    continue
                yield AuditRecord.from_dict(payload)

    async def aread_all(self) -> AsyncIterator[AuditRecord]:
        # Exposed for callers that prefer async iteration; the underlying
        # iterator is sync but small enough that wrapping it costs
        # nothing meaningful.
        for record in self.read_all():
            yield record

    async def close(self) -> None:
        # No long-lived file handle to release; appends open-and-close
        # per line so logrotate works without a SIGHUP dance.
        return None
