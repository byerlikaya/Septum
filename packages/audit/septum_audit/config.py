"""Operator-facing configuration for the audit service.

Like ``GatewayConfig``, the dataclass is intentionally minimal — the
sink path, the optional queue topic to consume from, and a retention
policy are everything the FastAPI app needs to wire itself up. Backends
(queue, sink) are still constructed by deployment code so the same
config can drive a file-only or queue-fed install.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AuditConfig:
    """Configuration surface for the audit consumer + exporter.

    ``sink_path`` defaults under ``/var/log`` to match the rest of the
    SIEM pipeline conventions; deployment scripts override it for the
    in-container default. ``audit_topic`` is the queue topic this
    service consumes from when running with the ``[queue]`` extra.
    """

    sink_path: str = "/var/log/septum/audit.jsonl"
    audit_topic: str = "septum.audit.events"
    retention_max_age_days: int | None = None
    retention_max_records: int | None = None

    @classmethod
    def from_env(cls) -> "AuditConfig":
        """Build a config entirely from ``SEPTUM_AUDIT_*`` environment variables."""

        def _opt_int(name: str) -> int | None:
            raw = os.getenv(name)
            if raw is None or raw == "":
                return None
            return int(raw)

        return cls(
            sink_path=os.getenv("SEPTUM_AUDIT_SINK_PATH", cls.sink_path),
            audit_topic=os.getenv("SEPTUM_AUDIT_TOPIC", cls.audit_topic),
            retention_max_age_days=_opt_int("SEPTUM_AUDIT_RETENTION_DAYS"),
            retention_max_records=_opt_int("SEPTUM_AUDIT_RETENTION_MAX_RECORDS"),
        )
