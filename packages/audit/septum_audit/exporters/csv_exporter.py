"""RFC 4180 CSV exporter. ``attributes`` is JSON-encoded into one cell."""

from __future__ import annotations

import csv
import json
from io import StringIO
from typing import Iterable, Iterator

from ..events import AuditRecord
from .base import BaseExporter

_HEADER = ["id", "timestamp", "source", "event_type", "correlation_id", "attributes"]


class CsvExporter(BaseExporter):
    content_type: str = "text/csv"
    file_extension: str = "csv"

    def iter_chunks(self, records: Iterable[AuditRecord]) -> Iterator[str]:
        buffer = StringIO()
        writer = csv.writer(buffer)
        first = True
        for record in records:
            if first:
                writer.writerow(_HEADER)
                first = False
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
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate()
