"""AuditConfig env-var loading."""

from __future__ import annotations

import os

import pytest

from septum_audit import AuditConfig
from septum_audit.config import DEFAULT_RETENTION_INTERVAL_SECONDS


@pytest.fixture(autouse=True)
def _isolate_audit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test from a clean SEPTUM_AUDIT_* environment so CI settings cannot leak in."""
    for name in list(os.environ):
        if name.startswith("SEPTUM_AUDIT_"):
            monkeypatch.delenv(name, raising=False)


def test_defaults():
    cfg = AuditConfig()
    assert cfg.sink_path.endswith("audit.jsonl")
    assert cfg.audit_topic == "septum.audit.events"
    assert cfg.retention_max_age_days is None
    assert cfg.retention_max_records is None
    assert cfg.retention_interval_seconds == DEFAULT_RETENTION_INTERVAL_SECONDS


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


@pytest.mark.parametrize(
    "name",
    [
        "SEPTUM_AUDIT_RETENTION_DAYS",
        "SEPTUM_AUDIT_RETENTION_MAX_RECORDS",
        "SEPTUM_AUDIT_RETENTION_INTERVAL_SECONDS",
    ],
)
@pytest.mark.parametrize("raw", ["0", "-5", "abc", "1.5"])
def test_from_env_rejects_invalid_retention_values(
    monkeypatch: pytest.MonkeyPatch, name: str, raw: str
):
    monkeypatch.setenv(name, raw)

    with pytest.raises(ValueError, match=name):
        AuditConfig.from_env()


def test_from_env_reads_retention_interval(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SEPTUM_AUDIT_RETENTION_INTERVAL_SECONDS", "600")

    assert AuditConfig.from_env().retention_interval_seconds == 600


def test_from_env_empty_interval_falls_back_to_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SEPTUM_AUDIT_RETENTION_INTERVAL_SECONDS", "")

    assert AuditConfig.from_env().retention_interval_seconds == DEFAULT_RETENTION_INTERVAL_SECONDS
