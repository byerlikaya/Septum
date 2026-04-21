"""FastAPI app smoke tests for /health and /api/audit/export."""

from __future__ import annotations

import csv
import json
from io import StringIO
from typing import Sequence

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from septum_audit import AuditConfig, AuditRecord, MemorySink  # noqa: E402
from septum_audit.main import create_app  # noqa: E402


def _build(records: Sequence[AuditRecord] | None = None) -> TestClient:
    sink = MemorySink(initial_records=records)
    cfg = AuditConfig(sink_path="ignored.jsonl", audit_topic="septum.audit.events")
    return TestClient(create_app(cfg, sink=sink))


def test_health_reports_topic_and_supported_formats():
    with _build() as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "septum-audit"
    assert body["audit_topic"] == "septum.audit.events"
    assert body["sink_path"] == "ignored.jsonl"
    assert sorted(body["supported_formats"]) == ["csv", "jsonl", "siem"]


def test_export_jsonl_default_returns_one_record_per_line():
    records = [
        AuditRecord(id="r-1", source="api", event_type="x", attributes={"a": 1}),
        AuditRecord(id="r-2", source="api", event_type="y", attributes={"b": 2}),
    ]
    with _build(records) as client:
        resp = client.get("/api/audit/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    assert 'attachment; filename="septum-audit.jsonl"' in resp.headers[
        "content-disposition"
    ]
    lines = resp.text.strip().split("\n")
    assert [json.loads(l)["id"] for l in lines] == ["r-1", "r-2"]


def test_export_csv_returns_flat_rows_with_attributes_cell():
    records = [
        AuditRecord(id="r-1", source="api", event_type="x", attributes={"k": "v"}),
    ]
    with _build(records) as client:
        resp = client.get("/api/audit/export?format=csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert 'filename="septum-audit.csv"' in resp.headers["content-disposition"]

    rows = list(csv.reader(StringIO(resp.text)))
    assert rows[0][0] == "id"
    assert rows[1][0] == "r-1"
    assert json.loads(rows[1][5]) == {"k": "v"}


def test_export_siem_wraps_records_in_hec_envelope():
    records = [
        AuditRecord(
            id="r-1",
            timestamp=1700000000.0,
            source="gateway",
            event_type="llm.request.completed",
            correlation_id="cor-1",
            attributes={"provider": "openai"},
        )
    ]
    with _build(records) as client:
        resp = client.get("/api/audit/export?format=siem")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert 'filename="septum-audit.hec.jsonl"' in resp.headers["content-disposition"]
    payload = json.loads(resp.text.strip())
    assert payload["sourcetype"] == "septum:audit"
    assert payload["event"]["correlation_id"] == "cor-1"


def test_export_unknown_format_returns_422_from_pydantic_validation():
    with _build() as client:
        resp = client.get("/api/audit/export?format=xml")
    # FastAPI's Literal validation surfaces as 422 with the allowed set.
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any("jsonl" in str(err).lower() for err in detail)


def test_export_with_empty_sink_returns_empty_body():
    with _build() as client:
        resp = client.get("/api/audit/export")
    assert resp.status_code == 200
    assert resp.text == ""
