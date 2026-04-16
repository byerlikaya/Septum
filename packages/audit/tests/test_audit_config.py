"""AuditConfig env-var loading."""

from __future__ import annotations

import os

import pytest

from septum_audit import AuditConfig


def test_defaults():
    cfg = AuditConfig()
    assert cfg.sink_path.endswith("audit.jsonl")
    assert cfg.audit_topic == "septum.audit.events"
    assert cfg.retention_max_age_days is None
    assert cfg.retention_max_records is None


def test_from_env_reads_overrides(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SEPTUM_AUDIT_SINK_PATH", "/data/audit.jsonl")
    monkeypatch.setenv("SEPTUM_AUDIT_TOPIC", "custom.topic")
    monkeypatch.setenv("SEPTUM_AUDIT_RETENTION_DAYS", "90")
    monkeypatch.setenv("SEPTUM_AUDIT_RETENTION_MAX_RECORDS", "1000000")

    cfg = AuditConfig.from_env()
    assert cfg.sink_path == "/data/audit.jsonl"
    assert cfg.audit_topic == "custom.topic"
    assert cfg.retention_max_age_days == 90
    assert cfg.retention_max_records == 1_000_000


def test_from_env_treats_empty_strings_as_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SEPTUM_AUDIT_RETENTION_DAYS", "")
    monkeypatch.setenv("SEPTUM_AUDIT_RETENTION_MAX_RECORDS", "")

    cfg = AuditConfig.from_env()
    assert cfg.retention_max_age_days is None
    assert cfg.retention_max_records is None
