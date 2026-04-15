from __future__ import annotations

"""FastAPI application entrypoint for Septum."""

import logging
import warnings

logger = logging.getLogger(__name__)

warnings.filterwarnings(
    "ignore",
    message=".*has conflict with protected namespace.*model_.*",
    category=UserWarning,
)

from . import bootstrap as _bootstrap_early
from .utils.logging_config import setup_structured_logging

setup_structured_logging(_bootstrap_early.get_config().log_level)

import threading  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from typing import Any  # noqa: E402

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.openapi.docs import get_redoc_html  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402
from sqlalchemy import select  # noqa: E402
from starlette import status as http_status  # noqa: E402

from . import bootstrap  # noqa: E402
from .database import (  # noqa: E402
    build_database_url,
    engine_is_ready,
    get_session_maker,
    init_db,
    initialize_engine,
)
from .models.settings import AppSettings
from .routers import approval as approval_router
from .routers import audit as audit_router
from .routers import auth as auth_router
from .routers import chat as chat_router
from .routers import chat_sessions as chat_sessions_router
from .routers import chunks as chunks_router
from .routers import documents as documents_router
from .routers import error_logs as error_logs_router
from .routers import regulations as regulations_router
from .routers import settings as settings_router
from .routers import setup as setup_router
from .routers import text_normalization as text_normalization_router
from .routers import users as users_router
from .services.error_logger import log_backend_error, log_backend_message
from .utils.device import get_device


def _preload_models() -> None:
    """Pre-load heavy ML models in a background thread.

    Called once at startup so that the first user request does not
    have to wait ~15 s for spaCy, NER, and embedding models to load.
    """
    try:
        from .services.sanitizer import _get_shared_nlp_engine
        _get_shared_nlp_engine()

        from .services.ner_model_registry import get_shared_ner_registry
        get_shared_ner_registry().get_pipeline("en")

        from .services.vector_store import get_shared_sentence_transformer
        model = get_shared_sentence_transformer()
        model.encode(["warmup"], convert_to_numpy=True)
    except Exception:
        pass


async def _reset_orphaned_processing_docs() -> None:
    """Mark documents stuck in 'processing' as 'failed' on startup.

    Background tasks are lost when the server restarts. Documents left
    in 'processing' status will never complete, so we mark them as
    failed so the user can reprocess them manually.
    """
    from sqlalchemy import update

    from .database import get_session_maker
    from .models.document import Document

    session_maker = get_session_maker()
    if session_maker is None:
        return
    async with session_maker() as db:
        result = await db.execute(
            update(Document)
            .where(Document.ingestion_status.in_(["processing", "pending"]))
            .values(
                ingestion_status="failed",
                ingestion_error="Processing interrupted by server restart. Please reprocess.",
            )
        )
        if result.rowcount > 0:
            await db.commit()
            logger.info("Reset %d orphaned processing documents to failed", result.rowcount)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan handler — initialise database if already configured."""
    config = bootstrap.get_config()
    if not bootstrap.needs_setup():
        url = build_database_url(config.database_url, config.db_path)
        initialize_engine(url)
        await init_db()
        await _reset_orphaned_processing_docs()
        threading.Thread(target=_preload_models, daemon=True).start()
    yield


from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

_boot_cfg = bootstrap.get_config()
_limiter_storage = (
    f"redis://{_boot_cfg.redis_url.split('://')[-1]}"
    if _boot_cfg.redis_url else "memory://"
)

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[_boot_cfg.rate_limit],
    storage_uri=_limiter_storage,
)

def _app_version() -> str:
    """Return the app version from the repo-root ``VERSION`` file.

    Walks up from ``__file__`` instead of using a fixed ``parents[N]``
    offset so the lookup is robust to future relocations.
    """
    from pathlib import Path
    for parent in Path(__file__).resolve().parents:
        try:
            return (parent / "VERSION").read_text().strip()
        except (FileNotFoundError, OSError):
            continue
    return "dev"


_APP_VERSION: str = _app_version()

app = FastAPI(
    title="Septum API",
    description=(
        "Privacy-first AI middleware. Anonymize documents, chat with LLMs, "
        "and keep raw PII local.\n\n"
        "**Tag groups:** setup (first-run wizard), settings (app config), "
        "documents (upload & ingestion), chat (RAG conversations), "
        "regulations (PII detection rules), audit (compliance trail)."
    ),
    version=_APP_VERSION,
    lifespan=lifespan,
    # FastAPI's default /redoc embeds `redoc@next`, which is the unstable
    # v3 alpha dist-tag and currently renders as a blank page. We serve
    # our own /redoc pinned to v2 (the stable line) instead.
    redoc_url=None,
)


@app.get("/redoc", include_in_schema=False, response_class=HTMLResponse)
async def redoc_html() -> HTMLResponse:
    return get_redoc_html(
        openapi_url=app.openapi_url or "/openapi.json",
        title=f"{app.title} - ReDoc",
        redoc_js_url="https://cdn.jsdelivr.net/npm/redoc@2/bundles/redoc.standalone.js",
    )
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

from .middleware.auth import AuthMiddleware
from .utils.metrics import PrometheusMiddleware, metrics_endpoint

app.add_middleware(AuthMiddleware)
app.add_middleware(PrometheusMiddleware)

# CORS must be the outermost middleware (added last = wraps everything)
# so that preflight OPTIONS responses always carry the correct headers.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_route("/metrics", metrics_endpoint, methods=["GET"])


# All API routers are mounted under both /api (legacy) and /api/v1 (versioned).
_all_routers = [
    setup_router.router,
    auth_router.router,
    approval_router.router,
    audit_router.router,
    documents_router.router,
    chunks_router.router,
    chat_router.router,
    chat_sessions_router.router,
    settings_router.router,
    error_logs_router.router,
    regulations_router.router,
    text_normalization_router.router,
    users_router.router,
]

for _r in _all_routers:
    app.include_router(_r)



@app.exception_handler(HTTPException)
async def http_exception_handler(
    request: Request, exc: HTTPException
) -> JSONResponse:
    """Return standard JSON for HTTPException so routes keep correct status codes.

    Also persists an error_log entry for any 4xx/5xx so the Error Logs UI
    surfaces these failures. 404s are excluded because they are noisy
    under healthchecks/probes; everything else 4xx+ is logged.

    5xx errors are routed through ``log_backend_error`` so the full
    ``traceback.format_exception`` stack is captured — otherwise the
    Error Logs "Detay" view lands on an entry with no stack trace, no
    exception type, and no pointer to the source file that raised, which
    makes repeat 500s impossible to diagnose in production. 4xx entries
    still use ``log_backend_message`` (WARNING level, no stack) because
    they are typically handled validation failures where the stack is
    noise rather than signal.
    """
    if exc.status_code >= 400 and exc.status_code != http_status.HTTP_404_NOT_FOUND:
        try:
            if engine_is_ready():
                sm = get_session_maker()
                async with sm() as db:
                    if exc.status_code >= 500:
                        await log_backend_error(
                            db=db,
                            request=request,
                            exc=exc,
                            level="ERROR",
                            status_code=exc.status_code,
                        )
                    else:
                        await log_backend_message(
                            db=db,
                            request=request,
                            message=(
                                str(exc.detail)
                                if exc.detail
                                else exc.__class__.__name__
                            ),
                            level="WARNING",
                            status_code=exc.status_code,
                        )
        except Exception:  # noqa: BLE001
            # Logging must never break the response.
            pass
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.exception_handler(Exception)
async def global_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Global fallback exception handler that also persists an error log entry."""
    try:
        if engine_is_ready():
            sm = get_session_maker()
            async with sm() as db:
                await log_backend_error(
                    db=db,
                    request=request,
                    exc=exc,
                    level="ERROR",
                    status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
    except Exception:
        pass

    return JSONResponse(
        status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "An unexpected error occurred while processing the request."
        },
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    """Health check endpoint that reports backend status and configuration."""
    from .services.redis_client import redis_ping

    result: dict[str, Any] = {
        "status": "ok",
        "version": _APP_VERSION,
        "device": get_device(),
        "setup_required": bootstrap.needs_setup(),
    }

    if engine_is_ready():
        try:
            sm = get_session_maker()
            async with sm() as db:
                row = await db.execute(select(AppSettings).where(AppSettings.id == 1))
                settings = row.scalar_one_or_none()
                if settings:
                    result["llm_provider"] = settings.llm_provider
        except Exception:
            pass

    from .services.llm_providers.health import get_all_statuses
    redis_ok = await redis_ping()
    result["redis"] = "connected" if redis_ok else "unavailable"
    result["llm_providers"] = get_all_statuses()

    return result

