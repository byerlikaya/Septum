"""CSV exporter that flattens the ``attributes`` map into ``key=value`` cells.

Spreadsheet pipelines and ad-hoc compliance reports want a flat shape;
nested JSON does not survive a round trip through Excel. The exporter
emits a fixed header (``id, timestamp, source, event_type,
correlation_id, attributes``) and serializes ``attributes`` as a
JSON-encoded cell so the structure is preserved without exploding the
column count per record.
"""

from __future__ import annotations

import csv
import json
from typing import IO, Iterable

from ..events import AuditRecord

_HEADER = ["id", "timestamp", "source", "event_type", "correlation_id", "attributes"]


class CsvExporter:
    """Stream :class:`AuditRecord` instances as RFC 4180 CSV."""

    content_type: str = "text/csv"
    file_extension: str = "csv"

    def write(self, records: Iterable[AuditRecord], out: IO[str]) -> int:
        writer = csv.writer(out)
        writer.writerow(_HEADER)
        count = 0
        for record in records:
            writer.writerow(
                [
                    record.id,
                    f"{record.timestamp:.6f}",
                    record.source,
                    record.event_type,
                    record.correlation_id or "",
                    json.dumps(record.attributes, separators=(",", ":")),
                ]
            )
            count += 1
        return count

    def to_string(self, records: Iterable[AuditRecord]) -> str:
        from io import StringIO

        buffer = StringIO()
        self.write(records, buffer)
        return buffer.getvalue()
