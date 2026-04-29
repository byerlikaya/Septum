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
        # ``QUOTE_ALL`` + explicit ``\n`` terminator pin RFC 4180-style
        # quoting so downstream re-parsers (Excel, BigQuery, Splunk
        # universal forwarder) cannot disagree on embedded quotes /
        # commas / newlines inside the JSON-serialised attributes cell.
        writer = csv.writer(
            buffer,
            quoting=csv.QUOTE_ALL,
            lineterminator="\n",
        )
        # Always emit a header row, even on an empty record set, so a
        # downstream "import not run" is distinguishable from a real
        # zero-row export.
        writer.writerow(_HEADER)
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate()
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
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate()
