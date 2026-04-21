"""Pluggable persistence backends for :class:`AuditRecord`."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Iterable, Iterator, Protocol, Sequence, runtime_checkable

from .events import AuditRecord


@runtime_checkable
class AuditSink(Protocol):
    """Storage contract every audit backend implements."""

    async def write(self, record: AuditRecord) -> None: ...

    def read_all(self) -> Iterable[AuditRecord]: ...

    async def close(self) -> None: ...


class MemorySink:
    """In-process list sink. Safe under asyncio; not thread-safe."""

    def __init__(self, initial_records: Sequence[AuditRecord] | None = None) -> None:
        self._records: list[AuditRecord] = list(initial_records or ())

    async def write(self, record: AuditRecord) -> None:
        self._records.append(record)

    def read_all(self) -> Iterator[AuditRecord]:
        # Snapshot so an iterator does not see records appear mid-iteration.
        return iter(list(self._records))

    def __len__(self) -> int:
        return len(self._records)

    async def close(self) -> None:
        return None


class JsonlFileSink:
    """Append-only newline-delimited JSON sink.

    Concurrent writers from different processes are safe because POSIX
    guarantees atomic appends below ``PIPE_BUF`` (4 KiB on Linux); audit
    lines are well under that limit. Per-line open-close is intentional —
    logrotate works without a SIGHUP dance.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._path

    async def write(self, record: AuditRecord) -> None:
        line = record.to_json()
        async with self._lock:
            await asyncio.to_thread(self._append, line)

    def _append(self, line: str) -> None:
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

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
