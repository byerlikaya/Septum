"""FastAPI entrypoint for the audit service.

Gated behind the ``[server]`` extra so a queue-only deployment does not
pull fastapi/uvicorn. Surface is intentionally tiny: ``/health`` and a
streaming ``/api/audit/export``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, AsyncIterator, Iterator, Literal

from .config import AuditConfig
from .consumer_status import read_consumer_status
from .exporters.base import BaseExporter
from .exporters.csv_exporter import CsvExporter
from .exporters.json_exporter import JsonExporter
from .exporters.siem_exporter import SplunkHecExporter
from .sink import AuditSink, JsonlFileSink
from .status import read_status

logger = logging.getLogger(__name__)


ExportFormat = Literal["jsonl", "csv", "siem"]

_EXPORTERS: dict[ExportFormat, type[BaseExporter]] = {
    "jsonl": JsonExporter,
    "csv": CsvExporter,
    "siem": SplunkHecExporter,
}


STATUS_GRACE_SECONDS = 300.0
CONSUMER_FAILURE_STREAK = 3
CONSUMER_FAILURE_AGE_SECONDS = 300.0


def _consumer_state(ledger: Path, now: float) -> tuple[str, bool]:
    """Return ``(state, healthy)`` for the consumer's heartbeat sidecar.

    A missing sidecar is healthy: no event may have arrived yet. An idle consumer writes
    nothing, so an old sidecar with no failure streak is healthy too.
    """
    try:
        status = read_consumer_status(ledger)
    except (ValueError, OSError):
        return "consumer_unreadable", False
    if status is None:
        return "not_reported", True
    if status.consecutive_failures >= CONSUMER_FAILURE_STREAK or (
        status.first_failure_at is not None and now - status.first_failure_at > CONSUMER_FAILURE_AGE_SECONDS
    ):
        return "consumer_failing", False
    return "ok", True


def _retention_state(ledger: Path, api_started_at: float, default_interval: int, now: float) -> tuple[str, bool]:
    """Return ``(state, healthy)`` from the worker's status sidecar.

    The worker checks in every tick, including while retention is disabled, so
    a check older than two intervals plus a grace period means the worker has
    stopped or cannot write. A missing sidecar is ``not_reported`` only during
    the first window after the API starts; after that it is ``missing``.
    """
    try:
        status = read_status(ledger)
    except (ValueError, OSError):
        return "unreadable", False
    if status is None:
        if now - api_started_at > 2 * default_interval + STATUS_GRACE_SECONDS:
            return "missing", False
        return "not_reported", True
    if now - status.checked_at > 2 * status.interval_seconds + STATUS_GRACE_SECONDS:
        return "stale", False
    return status.state, status.state not in ("halted", "failed", "blocked")


def _import_fastapi():
    try:
        from fastapi import Depends, FastAPI, Header, HTTPException, Query
        from fastapi.responses import JSONResponse, StreamingResponse
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "septum-audit[server] is required to run the FastAPI app. "
            "Install with: pip install 'septum-audit[server]'"
        ) from exc
    return FastAPI, Depends, Header, HTTPException, Query, StreamingResponse, JSONResponse


_CHUNKS_DONE = object()


async def _stream_chunks(first: Any, iterator: Iterator[str]) -> AsyncIterator[bytes]:
    """Yield the export body in chunks so the event loop is never blocked.

    ``first`` is the chunk already fetched before the response headers were sent.
    A failure part way through is logged by exception type and re-raised, so the
    server aborts the transfer and the client sees an incomplete body instead of a
    clean end. Pulling each chunk through ``asyncio.to_thread`` offloads the
    blocking read without materializing the whole file.
    """
    try:
        if first is not _CHUNKS_DONE:
            yield first.encode("utf-8")
            while True:
                chunk = await asyncio.to_thread(next, iterator, _CHUNKS_DONE)
                if chunk is _CHUNKS_DONE:
                    return
                yield chunk.encode("utf-8")
    except Exception as exc:
        logger.error("audit export aborted mid-stream: error=%s", type(exc).__name__)
        raise


def create_app(
    config: AuditConfig | None = None,
    *,
    sink: AuditSink | None = None,
) -> Any:
    """Build the FastAPI app. The caller may inject a sink for tests."""
    FastAPI, Depends, Header, HTTPException, Query, StreamingResponse, JSONResponse = _import_fastapi()
    cfg = config or AuditConfig.from_env()
    active_sink: AuditSink = sink if sink is not None else JsonlFileSink(cfg.sink_path)
    started_at = time.time()

    app = FastAPI(
        title="Septum Audit",
        description=(
            "Internet-facing compliance audit trail for Septum. Persists "
            "already-masked event records and exports them to JSON, CSV, "
            "or Splunk HEC. Never sees raw PII."
        ),
        version="0.1.0",
    )

    def _require_export_token(
        authorization: str | None = Header(default=None),
    ) -> None:
        """Reject /api/audit/export when SEPTUM_AUDIT_EXPORT_TOKEN is unset
        or the caller's bearer token does not match.

        The audit ledger sits in the internet-facing zone; without this
        gate, anyone reachable on the export port can stream every event
        the system has ever recorded.
        """
        import hmac

        if not cfg.export_token:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Audit export is disabled. Set SEPTUM_AUDIT_EXPORT_TOKEN "
                    "to enable the endpoint."
                ),
            )
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer token.")
        presented = authorization.split(" ", 1)[1].strip()
        if not hmac.compare_digest(presented, cfg.export_token):
            raise HTTPException(status_code=401, detail="Invalid bearer token.")

    @app.get("/health")
    async def health() -> Any:
        """Liveness, retention state and consumer state, read from the worker's sidecars.

        Returns 503 with ``status: degraded`` when retention is ``halted``, ``failed``,
        ``blocked``, ``stale`` or ``missing``, or its sidecar is ``unreadable``. It also
        does so when the consumer is ``consumer_failing`` (three or more consecutive
        failures, or a failure streak older than five minutes) or its sidecar is
        ``consumer_unreadable``. ``not_reported`` (nothing written yet) returns 200, and
        so does a missing consumer sidecar.
        """
        ledger = Path(cfg.sink_path)
        now = time.time()
        (retention, retention_ok), (consumer, consumer_ok) = await asyncio.gather(
            asyncio.to_thread(_retention_state, ledger, started_at, cfg.retention_interval_seconds, now),
            asyncio.to_thread(_consumer_state, ledger, now),
        )
        healthy = retention_ok and consumer_ok
        return JSONResponse(
            {
                "status": "ok" if healthy else "degraded",
                "service": "septum-audit",
                "audit_topic": cfg.audit_topic,
                "supported_formats": sorted(_EXPORTERS.keys()),
                "export_enabled": cfg.export_token is not None,
                "retention": retention,
                "consumer": consumer,
            },
            status_code=200 if healthy else 503,
        )

    @app.get("/api/audit/export", dependencies=[Depends(_require_export_token)])
    async def export(
        format: ExportFormat = Query(  # noqa: A002 (FastAPI param name)
            "jsonl",
            description="Export format. One of: jsonl, csv, siem.",
        ),
    ) -> Any:
        exporter = _EXPORTERS[format]()
        filename = f"septum-audit.{exporter.file_extension}"
        iterator = iter(exporter.iter_chunks(active_sink.read_all()))
        try:
            # Fetch the first chunk before the headers go out: a failure here is a clean 500.
            first = await asyncio.to_thread(next, iterator, _CHUNKS_DONE)
        except Exception as exc:
            logger.error("audit export failed before streaming began: error=%s", type(exc).__name__)
            raise HTTPException(status_code=500, detail="Audit export failed.") from None
        return StreamingResponse(
            _stream_chunks(first, iterator),
            media_type=exporter.content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store, max-age=0",
            },
        )

    return app
