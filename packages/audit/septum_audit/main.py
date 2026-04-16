"""FastAPI entrypoint for the audit service.

The HTTP surface is intentionally tiny: ``/health`` for an operator
liveness probe, and ``/api/audit/export`` for downloading the current
sink contents in JSON / CSV / Splunk HEC. Everything else (writing,
retention, queue consumption) is driven by long-lived processes wired
in by the deployment, not by HTTP.

Like the gateway, FastAPI / uvicorn are optional: this module works
only when the ``[server]`` extra is installed. The other audit modules
(events, sink, exporters, retention, consumer) have no FastAPI
dependency so a deployment that only writes records via the queue
consumer does not need to install the web stack.
"""

from __future__ import annotations

import logging
from io import StringIO
from typing import Any

from .config import AuditConfig
from .events import AuditRecord
from .exporters.csv_exporter import CsvExporter
from .exporters.json_exporter import JsonExporter
from .exporters.siem_exporter import SplunkHecExporter
from .sink import AuditSink, JsonlFileSink

logger = logging.getLogger(__name__)


# Map a public format string to its exporter factory. Keeping it as a
# dict makes adding a new format (Loki line protocol, OTLP JSON) a
# one-line change at the bottom of this module rather than another
# branch in the route handler.
_EXPORTERS = {
    "jsonl": JsonExporter,
    "csv": CsvExporter,
    "siem": SplunkHecExporter,
}


def _import_fastapi():
    try:
        from fastapi import FastAPI, HTTPException, Query, Response  # noqa: WPS433
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "septum-audit[server] is required to run the FastAPI app. "
            "Install with: pip install 'septum-audit[server]'"
        ) from exc
    return FastAPI, HTTPException, Query, Response


def create_app(
    config: AuditConfig | None = None,
    *,
    sink: AuditSink | None = None,
) -> Any:
    """Build the FastAPI app.

    The sink is constructed once at startup from ``config.sink_path``
    unless the caller passes one in (tests use the in-memory sink to
    avoid touching the filesystem). Same factory pattern as the
    gateway: deployment code owns backend lifetimes.
    """
    FastAPI, HTTPException, Query, Response = _import_fastapi()
    cfg = config or AuditConfig.from_env()
    active_sink: AuditSink = sink if sink is not None else JsonlFileSink(cfg.sink_path)

    app = FastAPI(
        title="Septum Audit",
        description=(
            "Internet-facing compliance audit trail for Septum. Persists "
            "already-masked event records and exports them to JSON, CSV, "
            "or Splunk HEC. Never sees raw PII."
        ),
        version="0.1.0",
    )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        # Like the gateway: minimal and PII-free.
        return {
            "status": "ok",
            "service": "septum-audit",
            "audit_topic": cfg.audit_topic,
            "sink_path": cfg.sink_path,
            "supported_formats": sorted(_EXPORTERS.keys()),
        }

    @app.get("/api/audit/export")
    async def export(
        format: str = Query(  # noqa: A002 (FastAPI param name)
            "jsonl",
            description=f"Export format. One of: {sorted(_EXPORTERS.keys())}",
        ),
    ) -> Any:
        """Stream the current sink contents in the requested format.

        Implementation note: the export reads the entire sink into a
        single response body. That is fine for the typical compliance
        slice (a day or two of records ≈ low MB), but a deployment that
        keeps months of data should rotate ``sink_path`` regularly via
        ``apply_retention_to_jsonl`` rather than relying on this
        endpoint to handle gigabyte-scale dumps.
        """
        try:
            exporter_cls = _EXPORTERS[format.lower()]
        except KeyError as exc:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"unsupported format {format!r}; "
                    f"choose one of {sorted(_EXPORTERS.keys())}"
                ),
            ) from exc

        exporter = exporter_cls()
        buffer = StringIO()
        records: list[AuditRecord] = list(active_sink.read_all())
        exporter.write(records, buffer)
        body = buffer.getvalue()

        filename = f"septum-audit.{exporter.file_extension}"
        return Response(
            content=body,
            media_type=exporter.content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Audit-Record-Count": str(len(records)),
            },
        )

    return app
