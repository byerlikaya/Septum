"""Newline-delimited JSON exporter (jq-pipeable, Loki/Vector/Fluent Bit native)."""

from __future__ import annotations

from typing import Iterable, Iterator

from ..events import AuditRecord, dumps_safe
from .base import BaseExporter


class JsonExporter(BaseExporter):
    content_type: str = "application/x-ndjson"
    file_extension: str = "jsonl"

    def iter_chunks(self, records: Iterable[AuditRecord]) -> Iterator[str]:
        for record in records:
            # Strict JSON: non-finite numbers from legacy lines are rendered as strings.
            # No depth cap: an export must reproduce what was stored.
            yield dumps_safe(record.to_dict(), max_depth=None) + "\n"
