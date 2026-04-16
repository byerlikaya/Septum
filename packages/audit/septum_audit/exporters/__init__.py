"""Format-specific writers that turn an :class:`AuditSink` into bytes.

Every exporter takes an iterable of :class:`AuditRecord` and writes a
single byte stream — JSON-lines, CSV, or Splunk HEC. They never touch
the sink's underlying storage so a single ``sink.read_all()`` can drive
all three formats in parallel.
"""

from .csv_exporter import CsvExporter
from .json_exporter import JsonExporter
from .siem_exporter import SplunkHecExporter

__all__ = ["JsonExporter", "CsvExporter", "SplunkHecExporter"]
