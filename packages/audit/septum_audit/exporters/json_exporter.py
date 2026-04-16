"""Newline-delimited JSON exporter (jq-pipeable, Loki/Vector/Fluent Bit native)."""

from __future__ import annotations

from typing import Iterable, Iterator

from ..events import AuditRecord
from .base import BaseExporter


class JsonExporter(BaseExporter):
    content_type: str = "application/x-ndjson"
    file_extension: str = "jsonl"

    def iter_chunks(self, records: Iterable[AuditRecord]) -> Iterator[str]:
        for record in records:
            yield record.to_json() + "\n"
