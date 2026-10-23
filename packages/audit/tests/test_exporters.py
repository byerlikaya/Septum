"""JSON, CSV and Splunk HEC export: formatting, round trips, and exports of legacy or deep ledgers that must stay strict and complete."""

from __future__ import annotations

import csv
import json
from io import StringIO
from pathlib import Path

import pytest

from septum_audit import AuditRecord, CsvExporter, JsonExporter, JsonlFileSink, SplunkHecExporter, verify_chain
from septum_audit.events import GENESIS_PREV_HASH

from _helpers import NOW, deep, legacy_ledger, strict_load


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
    rebuilt = [AuditRecord.from_dict(json.loads(line)) for line in out.strip().split("\n")]
    assert [r.id for r in rebuilt] == ["rid-1", "rid-2"]


def test_csv_exporter_writes_fixed_header_and_serializes_attributes():
    out = StringIO()
    # Header chunk + 2 record chunks; the user-visible contract is one row per record.
    count = CsvExporter().write(_records(), out)
    assert count == 3
    out.seek(0)
    rows = list(csv.reader(out))
    assert rows[0] == ["id", "timestamp", "source", "event_type", "correlation_id", "attributes"]
    assert rows[1][0] == "rid-1"
    assert json.loads(rows[1][5]) == {"provider": "openai", "latency_ms": 220}
    # Empty correlation id renders as the empty string, not "None".
    assert rows[2][4] == ""


def test_splunk_hec_exporter_wraps_records_in_envelope():
    out = StringIO()
    count = SplunkHecExporter(host="audit-1", sourcetype="septum:audit").write(_records(), out)
    assert count == 2
    payloads = [json.loads(line) for line in out.getvalue().strip().split("\n")]
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
    assert JsonExporter.content_type == "application/x-ndjson"
    assert JsonExporter.file_extension == "jsonl"
    assert CsvExporter.content_type == "text/csv"
    assert CsvExporter.file_extension == "csv"
    assert SplunkHecExporter.content_type == "application/json"
    assert SplunkHecExporter.file_extension == "hec.jsonl"


@pytest.mark.parametrize("exporter", [JsonExporter(), SplunkHecExporter()], ids=["jsonl", "siem"])
def test_legacy_non_finite_lines_export_as_strict_json_with_every_record(tmp_path: Path, exporter):
    target = tmp_path / "audit.jsonl"
    legacy_ledger(target)

    body = exporter.to_string(JsonlFileSink(target).read_all())
    lines = [line for line in body.split("\n") if line]

    assert len(lines) == 3
    parsed = [strict_load(line) for line in lines]
    ids = [item["event"]["id"] if "event" in item else item["id"] for item in parsed]
    assert ids == ["nan_ts", "inf_ts", "nan_attr"]


def test_legacy_non_finite_attributes_export_as_strings_in_csv(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    legacy_ledger(target)

    body = CsvExporter().to_string(JsonlFileSink(target).read_all())

    assert body.count("\n") == 4
    assert "NaN" in body and "Infinity" in body


def test_record_nested_40_levels_exports_in_full_and_the_export_reverifies(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    record = AuditRecord(
        id="deep40", timestamp=NOW, source="s", event_type="e", attributes={"deep": deep(40)}
    ).with_hash_chain(prev_hash=GENESIS_PREV_HASH)
    # Written the way v1.2.0 wrote it: no depth cap on the stored line.
    target.write_text(json.dumps(record.to_dict(), separators=(",", ":")) + "\n", encoding="utf-8")
    JsonlFileSink(target).verify()

    body = JsonExporter().to_string(JsonlFileSink(target).read_all())

    assert "<depth limit>" not in body
    exported = [AuditRecord.from_dict(json.loads(line)) for line in body.split("\n") if line]
    assert exported[0].attributes == {"deep": deep(40)}
    verify_chain(exported)


def test_export_keeps_an_overflowing_timestamp_line_as_nan_and_completes(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    r0 = AuditRecord(id="r0", timestamp=NOW, source="s", event_type="e").with_hash_chain(prev_hash=GENESIS_PREV_HASH)
    r1 = AuditRecord(id="r1", timestamp=NOW, source="s", event_type="e").with_hash_chain(prev_hash=r0.hash)
    big = '{"id":"big","timestamp":' + "1" * 400 + ',"source":"s","event_type":"e","attributes":{}}'
    target.write_text("\n".join([r0.to_json(), big, r1.to_json()]) + "\n", encoding="utf-8")

    body = JsonExporter().to_string(JsonlFileSink(target).read_all())

    assert [json.loads(line)["id"] for line in body.split("\n") if line] == ["r0", "big", "r1"]
