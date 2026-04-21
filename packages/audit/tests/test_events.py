"""Round-trip and default-value tests for AuditRecord."""

from __future__ import annotations

import json

from septum_audit import AuditRecord


def test_record_assigns_default_id_and_timestamp():
    rec = AuditRecord(source="septum-api", event_type="pii.detected")
    assert rec.id  # 32-char hex
    assert len(rec.id) == 32
    assert rec.timestamp > 0
    assert rec.attributes == {}
    assert rec.correlation_id is None


def test_record_round_trip_via_json_preserves_fields():
    rec = AuditRecord(
        source="septum-gateway",
        event_type="llm.request.completed",
        correlation_id="abc",
        attributes={"latency_ms": 312.5, "provider": "openai"},
    )
    payload = json.loads(rec.to_json())
    rebuilt = AuditRecord.from_dict(payload)
    assert rebuilt.id == rec.id
    assert rebuilt.timestamp == rec.timestamp
    assert rebuilt.source == rec.source
    assert rebuilt.event_type == rec.event_type
    assert rebuilt.correlation_id == "abc"
    assert rebuilt.attributes == {"latency_ms": 312.5, "provider": "openai"}


def test_from_dict_falls_back_to_defaults_when_fields_missing():
    rebuilt = AuditRecord.from_dict({})
    assert rebuilt.source == "unknown"
    assert rebuilt.event_type == "unknown"
    assert rebuilt.attributes == {}
    assert rebuilt.correlation_id is None


def test_from_dict_coerces_numeric_timestamp_strings():
    rebuilt = AuditRecord.from_dict({"timestamp": "1700000000.5"})
    assert rebuilt.timestamp == 1700000000.5
