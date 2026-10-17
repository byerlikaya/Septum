from __future__ import annotations

"""FastAPI router for the initial setup wizard.

These endpoints operate **without** a database connection so they can
run before the user has chosen a database backend. Auth is intentionally
not required — these endpoints are only meaningful during first-time setup.
"""

import asyncio
import json
import logging
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from .. import bootstrap
from ..database import (
    build_database_url,
    engine_is_ready,
    get_session_maker,
    init_db,
    initialize_engine,
)
from ..errors import public_error_text
from ..models.settings import AppSettings
from ..models.user import User
from ..services.whisper_models import (
    WhisperDownloadError,
    WhisperInstallBusy,
    install_whisper_file,
    resolve_whisper_target,
    whisper_file_plausible,
)
from ..utils.auth_dependency import require_admin_or_bootstrap, require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/setup", tags=["setup"])


def _read_version() -> str:
    """Read the application version from the repo-root ``VERSION`` file."""
    from ..main import _app_version
    return _app_version()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class SetupStatusResponse(BaseModel):
    """Current setup phase."""
    status: str  # "needs_infrastructure" | "needs_application_setup" | "complete"
    version: str = ""


class TestDatabaseRequest(BaseModel):
    """Payload for database connectivity test."""
    database_url: str


class TestRedisRequest(BaseModel):
    """Payload for Redis connectivity test."""
    redis_url: str


class TestConnectionResponse(BaseModel):
    """Generic connectivity test result."""
    ok: bool
    message: str = ""


class InitializeRequest(BaseModel):
    """Payload to initialise infrastructure."""
    database_type: str = "sqlite"  # "sqlite" | "postgresql"
    database_url: str = ""
    redis_url: str = ""


class InitializeResponse(BaseModel):
    """Result of infrastructure initialisation."""
    ok: bool
    message: str = ""


class InfrastructureResponse(BaseModel):
    """Current infrastructure configuration (sanitised)."""
    database_type: str  # "sqlite" | "postgresql"
    database_url_display: str  # masked for display
    redis_enabled: bool
    redis_url_display: str  # masked for display
    has_encryption_key: bool
    has_jwt_secret: bool
    log_level: str


class InfrastructureUpdateRequest(BaseModel):
    """Payload to update infrastructure settings."""
    database_type: str | None = None
    database_url: str | None = None
    redis_url: str | None = None
    log_level: str | None = None


# ---------------------------------------------------------------------------
# GET /api/setup/status
# ---------------------------------------------------------------------------

@router.get("/status", response_model=SetupStatusResponse)
async def setup_status() -> SetupStatusResponse:
    """Return the current setup phase — works without a database."""
    version = _read_version()

    if bootstrap.needs_setup():
        return SetupStatusResponse(status="needs_infrastructure", version=version)

    if not engine_is_ready():
        return SetupStatusResponse(status="needs_infrastructure", version=version)

    try:
        sm = get_session_maker()
        async with sm() as db:
            result = await db.execute(
                select(AppSettings.setup_completed).where(AppSettings.id == 1)
            )
            completed = result.scalar_one_or_none()
            if completed:
                return SetupStatusResponse(status="complete", version=version)
    except Exception as exc:
        logger.warning("Could not query setup_completed: %s", type(exc).__name__)

    return SetupStatusResponse(status="needs_application_setup", version=version)


# ---------------------------------------------------------------------------
# POST /api/setup/test-database
# ---------------------------------------------------------------------------

def _require_setup_phase() -> None:
    if not bootstrap.needs_setup():
        raise HTTPException(403, "Setup already completed.")


@router.post("/test-database", response_model=TestConnectionResponse)
async def test_database(body: TestDatabaseRequest, _: None = Depends(_require_setup_phase)) -> TestConnectionResponse:
    """Test connectivity to a PostgreSQL database."""
    url = body.database_url.strip()
    if not url:
        raise HTTPException(400, "database_url is required")

    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    try:
        eng = create_async_engine(url, poolclass=NullPool, connect_args={"timeout": 5})
        async with eng.connect() as conn:
            await conn.execute(select(1))
        await eng.dispose()
        return TestConnectionResponse(ok=True, message="Connection successful")
    except Exception as exc:
        logger.info("Database test failed: %s", type(exc).__name__)
        return TestConnectionResponse(ok=False, message=public_error_text(exc, "Database connection failed"))


# ---------------------------------------------------------------------------
# POST /api/setup/test-redis
# ---------------------------------------------------------------------------

def _get_redis_module():
    """Import redis.asyncio lazily."""
    import redis.asyncio as aioredis
    return aioredis


@router.post("/test-redis", response_model=TestConnectionResponse)
async def test_redis(body: TestRedisRequest, _: None = Depends(_require_setup_phase)) -> TestConnectionResponse:
    """Test connectivity to a Redis instance."""
    url = body.redis_url.strip()
    if not url:
        raise HTTPException(400, "redis_url is required")

    try:
        aioredis = _get_redis_module()
        client = aioredis.from_url(url, socket_connect_timeout=5)
        await client.ping()
        await client.aclose()
        return TestConnectionResponse(ok=True, message="Connection successful")
    except Exception as exc:
        logger.info("Redis test failed: %s", type(exc).__name__)
        return TestConnectionResponse(ok=False, message=public_error_text(exc, "Redis connection failed"))


# ---------------------------------------------------------------------------
# POST /api/setup/initialize
# ---------------------------------------------------------------------------

def _initialize_origin_check(request: Request) -> None:
    """Check setup-window origin only when initialization is actually needed.

    A no-op when the engine is already up (idempotent re-call) so the
    legitimate operator's repeat clicks don't fail with 403; otherwise
    requires loopback or ``X-Setup-Token``.
    """
    if engine_is_ready():
        return
    from ..utils.auth_dependency import _check_setup_window_origin

    _check_setup_window_origin(request)


@router.post("/initialize", response_model=InitializeResponse)
async def initialize(
    body: InitializeRequest,
    _: None = Depends(_initialize_origin_check),
) -> InitializeResponse:
    """Initialise the database engine and seed defaults.

    Called by the wizard after the user picks a database and cache backend.
    This endpoint writes ``config.json``, creates the engine, runs
    migrations (for PostgreSQL) and seeds default data.
    """
    if engine_is_ready():
        return InitializeResponse(ok=True, message="Already initialised")

    try:
        # Persist infrastructure choices
        bootstrap.save_config({
            "database_url": body.database_url.strip() if body.database_type == "postgresql" else "",
            "redis_url": body.redis_url.strip(),
            "database_configured": True,
        })

        # Build URL and create the engine
        config = bootstrap.get_config()
        url = build_database_url(config.database_url, config.db_path)
        initialize_engine(url)

        # Run Alembic migrations for PostgreSQL
        if config.database_url:
            await _run_alembic_upgrade(config.database_url)

        # Create tables (SQLite) and seed defaults
        await init_db()

        return InitializeResponse(ok=True, message="Infrastructure initialised")
    except Exception as exc:
        logger.error("Infrastructure initialisation failed: %s", type(exc).__name__)
        return InitializeResponse(
            ok=False, message=public_error_text(exc, "Infrastructure initialisation failed")
        )


def _mask_url(url: str) -> str:
    """Mask password in a URL for display."""
    if not url:
        return ""
    import re
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", url)


# ---------------------------------------------------------------------------
# GET /api/setup/infrastructure
# ---------------------------------------------------------------------------

@router.get("/infrastructure", response_model=InfrastructureResponse)
async def get_infrastructure(
    _user: User = Depends(require_role("admin")),
) -> InfrastructureResponse:
    """Return current infrastructure configuration for the settings page."""
    config = bootstrap.get_config()
    return InfrastructureResponse(
        database_type="postgresql" if config.database_url else "sqlite",
        database_url_display=_mask_url(config.database_url),
        redis_enabled=bool(config.redis_url),
        redis_url_display=_mask_url(config.redis_url),
        has_encryption_key=bool(config.encryption_key),
        has_jwt_secret=bool(config.jwt_secret_key),
        log_level=config.log_level,
    )


# ---------------------------------------------------------------------------
# PATCH /api/setup/infrastructure
# ---------------------------------------------------------------------------

@router.patch("/infrastructure", response_model=InitializeResponse)
async def update_infrastructure(
    body: InfrastructureUpdateRequest,
    _user: User = Depends(require_role("admin")),
) -> InitializeResponse:
    """Update infrastructure settings from the settings page.

    Unlike ``/initialize`` (wizard-only), this allows reconfiguring
    an already-running system. Changes to ``database_url`` require
    engine re-initialisation; changes to ``redis_url`` or ``log_level``
    take effect on the next request / restart.
    """
    try:
        updates: dict = {}
        config = bootstrap.get_config()

        if body.database_type is not None:
            if body.database_type == "sqlite":
                updates["database_url"] = ""
            elif body.database_url:
                updates["database_url"] = body.database_url.strip()

        if body.redis_url is not None:
            updates["redis_url"] = body.redis_url.strip()

        if body.log_level is not None:
            updates["log_level"] = body.log_level.strip()

        if updates:
            bootstrap.save_config(updates)

        new_config = bootstrap.get_config()
        db_url_changed = new_config.database_url != config.database_url

        if db_url_changed and engine_is_ready():
            url = build_database_url(new_config.database_url, new_config.db_path)
            initialize_engine(url)
            if new_config.database_url:
                await _run_alembic_upgrade(new_config.database_url)
            await init_db()

        return InitializeResponse(ok=True, message="Configuration updated")
    except Exception as exc:
        logger.error("Infrastructure update failed: %s", type(exc).__name__)
        return InitializeResponse(ok=False, message=public_error_text(exc, "Infrastructure update failed"))


# ---------------------------------------------------------------------------
# Whisper model files
# ---------------------------------------------------------------------------

def _sse(payload: dict) -> str:
    """Encode one server-sent event."""
    return f"data: {json.dumps(payload)}\n\n"


# ---------------------------------------------------------------------------
# GET /api/setup/whisper-status
# ---------------------------------------------------------------------------

class WhisperStatusResponse(BaseModel):
    """Whisper model availability check."""
    installed: bool
    model: str
    message: str = ""


@router.get("/whisper-status", response_model=WhisperStatusResponse)
async def whisper_status(
    model: str = "base",
    _: User | None = Depends(require_admin_or_bootstrap),
) -> WhisperStatusResponse:
    """Report whether a Whisper model looks installed, without hashing it.

    Open to the wizard during bootstrap, admin-only afterwards. The check is
    stat-based so it stays cheap; the install path runs the full SHA256
    verification.
    """
    try:
        import whisper  # type: ignore[import]
    except ImportError:
        return WhisperStatusResponse(installed=False, model=model, message="whisper package not available")

    try:
        target = resolve_whisper_target(getattr(whisper, "_MODELS", {}), model)
    except WhisperDownloadError as exc:
        return WhisperStatusResponse(installed=False, model=model, message=str(exc))
    if whisper_file_plausible(target):
        return WhisperStatusResponse(installed=True, model=model, message="Model ready")
    return WhisperStatusResponse(installed=False, model=model, message="Model not downloaded")


# ---------------------------------------------------------------------------
# POST /api/setup/install-whisper
# ---------------------------------------------------------------------------

class InstallWhisperRequest(BaseModel):
    """Payload to install a Whisper model."""
    model: str = "base"


@router.post("/install-whisper")
async def install_whisper(
    body: InstallWhisperRequest,
    _: User | None = Depends(require_admin_or_bootstrap),
):
    """Download a Whisper model with SSE progress streaming."""
    from fastapi.responses import StreamingResponse

    model_name = body.model.strip() or "base"

    try:
        import whisper  # type: ignore[import]
    except ImportError:
        return TestConnectionResponse(ok=False, message="openai-whisper package not installed")

    try:
        target = resolve_whisper_target(getattr(whisper, "_MODELS", {}), model_name)
    except WhisperDownloadError as exc:
        status = str(exc)

        async def _refused():
            yield _sse({"percent": 0, "status": status, "error": True})
        return StreamingResponse(_refused(), media_type="text/event-stream")

    # Download with direct byte-level progress tracking instead of
    # polling file size (which is unreliable under Docker/QEMU I/O). Each
    # attempt takes the model's lock without blocking, so a concurrent
    # install never parks an executor thread; this request polls instead.
    progress = {"downloaded": 0, "total": target.approx_bytes}

    def _percent() -> int:
        total = progress["total"]
        return min(int(progress["downloaded"] * 100 / total), 99) if total > 0 else 0

    def _settle_abandoned(task: asyncio.Future) -> None:
        """Collect the outcome of an attempt whose client went away."""
        if not task.cancelled() and task.exception() is not None:
            logger.warning(
                "Whisper model download failed after client left: %s",
                type(task.exception()).__name__,
            )

    async def _stream_progress():
        yield _sse({"percent": 0, "status": "downloading"})
        give_up = time.monotonic() + target.deadline_seconds
        while True:
            attempt = asyncio.ensure_future(
                asyncio.to_thread(install_whisper_file, target, progress, wait=False)
            )
            try:
                while not attempt.done():
                    await asyncio.wait({attempt}, timeout=1)
                    if not attempt.done():
                        yield _sse({"percent": _percent(), "status": "downloading"})
            finally:
                if not attempt.done():
                    # The worker thread cannot be interrupted; it finishes
                    # under its deadline and releases the locks on its own.
                    attempt.add_done_callback(_settle_abandoned)
            failure = attempt.exception()
            if isinstance(failure, WhisperInstallBusy) and time.monotonic() < give_up:
                yield _sse({"percent": 0, "status": "waiting"})
                await asyncio.sleep(1)
                continue
            break

        if failure is None:
            yield _sse({"percent": 100, "status": "ready", "done": True})
        else:
            logger.warning("Whisper model download failed: %s", type(failure).__name__)
            yield _sse({"percent": 0, "status": public_error_text(failure, "Model download failed"), "error": True})

    return StreamingResponse(_stream_progress(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# GET /api/setup/check-update
# ---------------------------------------------------------------------------

class UpdateCheckResponse(BaseModel):
    """Version update check result."""
    current_version: str
    latest_version: str
    update_available: bool
    update_command: str = ""


@router.get("/check-update", response_model=UpdateCheckResponse)
async def check_update() -> UpdateCheckResponse:
    """Check Docker Hub for a newer version of the Septum image."""
    current = _read_version()

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://hub.docker.com/v2/repositories/byerlikaya/septum/tags",
                params={"page_size": 50, "ordering": "last_updated"},
            )
            resp.raise_for_status()
            tags = resp.json().get("results", [])

        # Find the highest semantic version tag (skip "latest")
        latest = current
        for tag in tags:
            name = tag.get("name", "")
            if name == "latest" or not name[0].isdigit():
                continue
            parts = name.split(".")
            if len(parts) == 3:
                try:
                    [int(p) for p in parts]
                    if _version_gt(name, latest):
                        latest = name
                except ValueError:
                    continue

        update_available = _version_gt(latest, current)
        return UpdateCheckResponse(
            current_version=current,
            latest_version=latest,
            update_available=update_available,
            update_command=f"docker pull byerlikaya/septum:{latest}" if update_available else "",
        )
    except Exception as exc:
        logger.info("Update check failed: %s", type(exc).__name__)
        return UpdateCheckResponse(
            current_version=current,
            latest_version=current,
            update_available=False,
        )


def _version_gt(a: str, b: str) -> bool:
    """Return True if version *a* is greater than version *b*."""
    try:
        a_parts = [int(x) for x in a.split(".")]
        b_parts = [int(x) for x in b.split(".")]
        return a_parts > b_parts
    except ValueError:
        return False


async def _run_alembic_upgrade(database_url: str) -> None:
    """Run Alembic migrations programmatically."""
    import asyncio
    import os
    import subprocess

    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    result = await asyncio.to_thread(
        subprocess.run,
        ["python", "-m", "alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[2]),
    )
    if result.returncode != 0:
        raise RuntimeError(f"Alembic migration failed: {result.stderr}")
