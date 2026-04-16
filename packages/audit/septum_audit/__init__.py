"""Septum compliance audit trail: append-only records + exporters.

``septum-audit`` runs in the internet-facing zone alongside
``septum-gateway``. It persists already-masked event records (no raw PII)
and ships them to downstream compliance pipelines (JSON, CSV, Splunk
HEC). The package itself has zero runtime dependencies; the queue
consumer and FastAPI export endpoint live behind the ``[queue]`` and
``[server]`` extras so a minimal install pulls nothing extra.

Like the gateway, ``septum-audit`` MUST NOT import ``septum-core``. The
dependency wall is what keeps raw PII out of the audit zone — every
record this package writes is an opaque dict the producer already
scrubbed.
"""

from __future__ import annotations

from typing import Any

from .config import AuditConfig
from .events import AuditRecord
from .retention import RetentionPolicy, apply_retention_to_jsonl
from .sink import AuditSink, JsonlFileSink, MemorySink

__all__ = [
    "AuditConfig",
    "AuditRecord",
    "AuditSink",
    "JsonlFileSink",
    "MemorySink",
    "RetentionPolicy",
    "apply_retention_to_jsonl",
    "JsonExporter",
    "CsvExporter",
    "SplunkHecExporter",
]

__version__ = "0.1.0"


def __getattr__(name: str) -> Any:
    """Lazy resolver for exporters so importers only pay for what they use."""
    if name == "JsonExporter":
        from .exporters.json_exporter import JsonExporter

        return JsonExporter
    if name == "CsvExporter":
        from .exporters.csv_exporter import CsvExporter

        return CsvExporter
    if name == "SplunkHecExporter":
        from .exporters.siem_exporter import SplunkHecExporter

        return SplunkHecExporter
    raise AttributeError(f"module 'septum_audit' has no attribute {name!r}")
