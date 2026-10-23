"""Operator-facing configuration for the audit service."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_RETENTION_INTERVAL_SECONDS = 3600


def _positive_int(name: str) -> int | None:
    """Return the variable as an int >= 1, ``None`` when unset or empty.

    Invalid values raise ``ValueError`` naming the variable: a retention
    cap that silently fell back to "keep everything" (or "delete everything")
    is worse than a refused start.
    """
    raw = os.getenv(name)
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number") from None
    if value < 1:
        raise ValueError(f"{name} must be at least 1")
    return value


@dataclass(frozen=True)
class AuditConfig:
    """Configuration surface for the audit consumer + exporter."""

    sink_path: str = "/var/log/septum/audit.jsonl"
    audit_topic: str = "septum.audit.events"
    retention_max_age_days: int | None = None
    retention_max_records: int | None = None
    retention_interval_seconds: int = DEFAULT_RETENTION_INTERVAL_SECONDS
    export_token: str | None = None

    @classmethod
    def from_env(cls) -> "AuditConfig":
        """Build a config from ``SEPTUM_AUDIT_*`` environment variables."""
        token = os.getenv("SEPTUM_AUDIT_EXPORT_TOKEN") or None

        return cls(
            sink_path=os.getenv("SEPTUM_AUDIT_SINK_PATH", cls.sink_path),
            audit_topic=os.getenv("SEPTUM_AUDIT_TOPIC", cls.audit_topic),
            retention_max_age_days=_positive_int("SEPTUM_AUDIT_RETENTION_DAYS"),
            retention_max_records=_positive_int("SEPTUM_AUDIT_RETENTION_MAX_RECORDS"),
            retention_interval_seconds=(
                _positive_int("SEPTUM_AUDIT_RETENTION_INTERVAL_SECONDS")
                or DEFAULT_RETENTION_INTERVAL_SECONDS
            ),
            export_token=token,
        )
