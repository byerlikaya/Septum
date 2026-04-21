"""FastAPI entrypoint for the audit service.

Gated behind the ``[server]`` extra so a queue-only deployment does not
pull fastapi/uvicorn. Surface is intentionally tiny: ``/health`` and a
streaming ``/api/audit/export``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Iterable, Literal

from .config import AuditConfig
from .events import AuditRecord
from .exporters.base import BaseExporter
from .exporters.csv_exporter import CsvExporter
from .exporters.json_exporter import JsonExporter
from .exporters.siem_exporter import SplunkHecExporter
from .sink import AuditSink, JsonlFileSink

logger = logging.getLogger(__name__)


ExportFormat = Literal["jsonl", "csv", "siem"]

_EXPORTERS: dict[ExportFormat, type[BaseExporter]] = {
    "jsonl": JsonExporter,
    "csv": CsvExporter,
    "siem": SplunkHecExporter,
}


def _import_fastapi():
    try:
        from fastapi import FastAPI, Query
        from fastapi.responses import StreamingResponse
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "septum-audit[server] is required to run the FastAPI app. "
            "Install with: pip install 'septum-audit[server]'"
        ) from exc
    return FastAPI, Query, StreamingResponse


async def _stream_export(
    exporter: BaseExporter, records: Iterable[AuditRecord]
) -> AsyncIterator[bytes]:
    """Yield the export body in chunks so the event loop is never blocked.

    ``records`` is a sync iterator (the sink is stdlib-backed); pulling
    each chunk through ``asyncio.to_thread`` offloads the blocking read
    without materializing the whole file.
    """
    iterator = iter(exporter.iter_chunks(records))
    sentinel = object()
    while True:
        chunk = await asyncio.to_thread(next, iterator, sentinel)
        if chunk is sentinel:
            return
        yield chunk.encode("utf-8")


def create_app(
    config: AuditConfig | None = None,
    *,
    sink: AuditSink | None = None,
) -> Any:
    """Build the FastAPI app. The caller may inject a sink for tests."""
    FastAPI, Query, StreamingResponse = _import_fastapi()
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
        return {
            "status": "ok",
            "service": "septum-audit",
            "audit_topic": cfg.audit_topic,
            "sink_path": cfg.sink_path,
            "supported_formats": sorted(_EXPORTERS.keys()),
        }

    @app.get("/api/audit/export")
    async def export(
        format: ExportFormat = Query(  # noqa: A002 (FastAPI param name)
            "jsonl",
            description="Export format. One of: jsonl, csv, siem.",
        ),
    ) -> Any:
        exporter = _EXPORTERS[format]()
        filename = f"septum-audit.{exporter.file_extension}"
        return StreamingResponse(
            _stream_export(exporter, active_sink.read_all()),
            media_type=exporter.content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    return app
