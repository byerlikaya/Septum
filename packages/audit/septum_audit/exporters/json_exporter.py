"""Newline-delimited JSON exporter.

Matches the on-disk shape of :class:`JsonlFileSink` so an operator can
``cat audit.jsonl`` for a slice and pipe the same bytes through ``jq``
or feed them straight to a Loki / Vector / Fluent Bit ingest. Choosing
jsonl over a top-level JSON array means the exporter streams record by
record without ever materializing the full collection in memory.
"""

from __future__ import annotations

from typing import IO, Iterable

from ..events import AuditRecord


class JsonExporter:
    """Stream :class:`AuditRecord` instances as newline-delimited JSON."""

    content_type: str = "application/x-ndjson"
    file_extension: str = "jsonl"

    def write(self, records: Iterable[AuditRecord], out: IO[str]) -> int:
        """Write each record on its own line. Returns the count emitted."""
        count = 0
        for record in records:
            out.write(record.to_json())
            out.write("\n")
            count += 1
        return count

    def to_string(self, records: Iterable[AuditRecord]) -> str:
        """Convenience for callers that want the whole dump as a string."""
        from io import StringIO

        buffer = StringIO()
        self.write(records, buffer)
        return buffer.getvalue()
