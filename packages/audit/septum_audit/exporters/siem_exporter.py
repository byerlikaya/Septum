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
        self._host = self._sanitize_metadata("host", host)
        self._sourcetype = self._sanitize_metadata("sourcetype", sourcetype)
        self._index = (
            self._sanitize_metadata("index", index) if index is not None else None
        )

    @staticmethod
    def _sanitize_metadata(name: str, value: str) -> str:
        """Reject control characters that would split the HEC NDJSON line.

        An attacker who could plant ``\\n`` (or any unprintable) into
        ``host``/``sourcetype``/``index`` could synthesize a second
        forged HEC event downstream by breaking the line frame. Refuse
        loud rather than letting a typo become an injection vector.
        """
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")
        for ch in value:
            if ch in {"\r", "\n", "\t"} or not ch.isprintable():
                raise ValueError(f"{name} contains a disallowed control character")
        if len(value) > 256:
            raise ValueError(f"{name} exceeds 256 characters")
        return value

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
