"""Splunk HTTP Event Collector (HEC) line-delimited exporter.

Covers Cribl / Elastic / Loki Splunk-compatible ingest modes too.
Reference: https://docs.splunk.com/Documentation/Splunk/latest/Data/FormateventsforHTTPEventCollector
"""

from __future__ import annotations

import json
from typing import Iterable, Iterator

from ..events import AuditRecord
from .base import BaseExporter


class SplunkHecExporter(BaseExporter):
    content_type: str = "application/json"
    file_extension: str = "hec.jsonl"

    def __init__(
        self,
        *,
        host: str = "septum-audit",
        sourcetype: str = "septum:audit",
        index: str | None = None,
    ) -> None:
        self._host = host
        self._sourcetype = sourcetype
        self._index = index

    def iter_chunks(self, records: Iterable[AuditRecord]) -> Iterator[str]:
        for record in records:
            envelope: dict[str, object] = {
                "time": record.timestamp,
                "host": self._host,
                "source": record.source,
                "sourcetype": self._sourcetype,
                "event": {
                    "id": record.id,
                    "event_type": record.event_type,
                    "correlation_id": record.correlation_id,
                    "attributes": record.attributes,
                },
            }
            if self._index is not None:
                envelope["index"] = self._index
            yield json.dumps(envelope, separators=(",", ":")) + "\n"
