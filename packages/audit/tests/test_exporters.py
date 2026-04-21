"""JSON / CSV / Splunk HEC exporter formatting and round-trip tests."""

from __future__ import annotations

import csv
import json
from io import StringIO

from septum_audit import (
    AuditRecord,
    CsvExporter,
    JsonExporter,
    SplunkHecExporter,
)


def _records():
    return [
        AuditRecord(
            id="rid-1",
            timestamp=1700000000.0,
            source="septum-gateway",
            event_type="llm.request.completed",
            correlation_id="cor-1",
            attributes={"provider": "openai", "latency_ms": 220},
        ),
        AuditRecord(
            id="rid-2",
            timestamp=1700000005.5,
            source="septum-api",
            event_type="pii.detected",
            correlation_id=None,
            attributes={"entity_count": 7},
        ),
    ]


def test_json_exporter_emits_one_record_per_line():
    out = StringIO()
    count = JsonExporter().write(_records(), out)
    assert count == 2
    lines = out.getvalue().strip().split("\n")
    assert len(lines) == 2
    payloads = [json.loads(line) for line in lines]
    assert payloads[0]["event_type"] == "llm.request.completed"
    assert payloads[1]["attributes"]["entity_count"] == 7


def test_json_exporter_roundtrips_via_audit_record():
    out = JsonExporter().to_string(_records())
    rebuilt = [AuditRecord.from_dict(json.loads(l)) for l in out.strip().split("\n")]
    assert [r.id for r in rebuilt] == ["rid-1", "rid-2"]


def test_csv_exporter_writes_fixed_header_and_serializes_attributes():
    out = StringIO()
    count = CsvExporter().write(_records(), out)
    assert count == 2
    out.seek(0)
    rows = list(csv.reader(out))
    assert rows[0] == [
        "id",
        "timestamp",
        "source",
        "event_type",
        "correlation_id",
        "attributes",
    ]
    assert rows[1][0] == "rid-1"
    assert json.loads(rows[1][5]) == {"provider": "openai", "latency_ms": 220}
    # Empty correlation id renders as the empty string, not "None".
    assert rows[2][4] == ""


def test_splunk_hec_exporter_wraps_records_in_envelope():
    out = StringIO()
    count = SplunkHecExporter(host="audit-1", sourcetype="septum:audit").write(
        _records(), out
    )
    assert count == 2
    payloads = [json.loads(l) for l in out.getvalue().strip().split("\n")]
    assert payloads[0]["host"] == "audit-1"
    assert payloads[0]["sourcetype"] == "septum:audit"
    assert payloads[0]["source"] == "septum-gateway"
    assert payloads[0]["time"] == 1700000000.0
    event = payloads[0]["event"]
    assert event["id"] == "rid-1"
    assert event["event_type"] == "llm.request.completed"
    assert event["correlation_id"] == "cor-1"
    assert event["attributes"] == {"provider": "openai", "latency_ms": 220}


def test_splunk_hec_exporter_includes_index_when_set():
    out = StringIO()
    SplunkHecExporter(index="security_audit").write(_records()[:1], out)
    payload = json.loads(out.getvalue().strip())
    assert payload["index"] == "security_audit"


def test_exporters_advertise_content_type_and_extension():
    # Used by the FastAPI route to set Content-Disposition / Content-Type.
    assert JsonExporter.content_type == "application/x-ndjson"
    assert JsonExporter.file_extension == "jsonl"
    assert CsvExporter.content_type == "text/csv"
    assert CsvExporter.file_extension == "csv"
    assert SplunkHecExporter.content_type == "application/json"
    assert SplunkHecExporter.file_extension == "hec.jsonl"
