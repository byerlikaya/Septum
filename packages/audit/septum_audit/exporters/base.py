"""Shared exporter contract: chunked iteration + buffered write + string dump."""

from __future__ import annotations

from abc import ABC, abstractmethod
from io import StringIO
from typing import IO, Iterable, Iterator

from ..events import AuditRecord


class BaseExporter(ABC):
    """Every concrete exporter yields one string per logical output unit.

    ``iter_chunks`` is the streaming primitive (one header line, then one
    line per record for csv; one line per record for jsonl / siem).
    ``write`` and ``to_string`` are thin wrappers so callers can pick
    between stream, IO, or one-shot string without three near-identical
    implementations.
    """

    content_type: str
    file_extension: str

    @abstractmethod
    def iter_chunks(self, records: Iterable[AuditRecord]) -> Iterator[str]: ...

    def write(self, records: Iterable[AuditRecord], out: IO[str]) -> int:
        count = 0
        for chunk in self.iter_chunks(records):
            out.write(chunk)
            count += 1
        return count

    def to_string(self, records: Iterable[AuditRecord]) -> str:
        buffer = StringIO()
        self.write(records, buffer)
        return buffer.getvalue()
