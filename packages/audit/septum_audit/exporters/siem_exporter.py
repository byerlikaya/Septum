"""Splunk HTTP Event Collector (HEC) exporter.

Splunk's HEC accepts newline-delimited JSON envelopes with a small
required wrapper (``time``, ``host``, ``source``, ``sourcetype``,
``event``). Most enterprise SIEM pipelines (Splunk Cloud, Cribl, the
Splunk-compatible mode in Elastic / Loki) consume the same shape, so
emitting HEC also covers ``ingest_pipeline``-style workflows in those
tools without a separate exporter per vendor.

Reference: https://docs.splunk.com/Documentation/Splunk/latest/Data/FormateventsforHTTPEventCollector
"""

from __future__ import annotations

import json
from typing import IO, Iterable

from ..events import AuditRecord


class SplunkHecExporter:
    """Stream :class:`AuditRecord` instances as Splunk HEC envelopes."""

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

    def write(self, records: Iterable[AuditRecord], out: IO[str]) -> int:
        count = 0
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
            out.write(json.dumps(envelope, separators=(",", ":")))
            out.write("\n")
            count += 1
        return count

    def to_string(self, records: Iterable[AuditRecord]) -> str:
        from io import StringIO

        buffer = StringIO()
        self.write(records, buffer)
        return buffer.getvalue()
