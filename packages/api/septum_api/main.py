from __future__ import annotations

"""FastAPI application entrypoint for Septum."""

import asyncio
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

import os
import threading  # noqa: E402
from contextlib import asynccontextmanager, suppress  # noqa: E402
from typing import Any  # noqa: E402

from fastapi import Depends, FastAPI, HTTPException, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.openapi.docs import get_redoc_html  # noqa: E402
from fastapi.openapi.utils import get_openapi  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402
from prometheus_client import Counter  # noqa: E402
from septum_core.model_integrity import (
    ModelRefusal,
    parse_model_spec,
    split_model_specs,
)
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
from .env_flags import parse_bool_default_flags
from .errors import MODEL_REFUSED_DETAIL
from .models.settings import AppSettings
from .routers import api_keys as api_keys_router
from .routers import approval as approval_router
from .routers import audit as audit_router
from .routers import auth as auth_router
from .routers import chat as chat_router
from .routers import chat_sessions as chat_sessions_router
from .routers import chunks as chunks_router
from .routers import documents as documents_router
from .routers import error_logs as error_logs_router
from .routers import regulations as regulations_router
from .routers import relationships as relationships_router
from .routers import settings as settings_router
from .routers import setup as setup_router
from .routers import text_normalization as text_normalization_router
from .routers import users as users_router
from .services.document_anon_store import sweep_stale_temp_files
from .services.error_logger import (
    log_backend_error,
    log_backend_message,
    purge_expired_error_logs,
    report_error_log_failure,
)
from .services.llm_errors import GatewayStartupError
from .services.llm_router import GatewayClientProtocol, set_gateway_client_factory
from .utils.auth_dependency import setup_window_open
from .utils.device import get_device

MODEL_WARMUP_OPT_OUT_ENV = "SEPTUM_DISABLE_MODEL_WARMUP"
QUEUE_ENV_VARS = ("SEPTUM_QUEUE_URL", "SEPTUM_QUEUE_DIR")


def _warn_on_refused_model_settings(
    ner_overrides: dict[str, str] | None, whisper_model: str | None
) -> None:
    """Log one warning, codes only, if a stored model setting would be refused at use.

    The Whisper check is skipped when openai-whisper is not installed: the
    registry is unknown then, and audio ingestion reports that on its own.
    """
    import importlib.util

    from .services.whisper_models import require_registry_model

    codes: set[str] = set()
    for spec in split_model_specs(ner_overrides):
        try:
            parse_model_spec(spec)
        except ModelRefusal as exc:
            codes.add(exc.code)
    if whisper_model and importlib.util.find_spec("whisper") is not None:
        try:
            require_registry_model(whisper_model)
        except ModelRefusal as exc:
            codes.add(exc.code)
    if codes:
        logger.warning(
            "Stored model settings would be refused at use: %s", ", ".join(sorted(codes))
        )


def _preload_models(
    ner_overrides: dict[str, str] | None = None, whisper_model: str | None = None
) -> None:
    """Pre-load heavy ML models in a background thread.

    Called once at startup so that the first user request does not
    have to wait ~15 s for spaCy, NER, and embedding models to load, after
    warning about stored model settings that would be refused. Warmup is
    best-effort and never fatal: any failure, including a model refused by
    the integrity check, is logged as a warning, and the request that needs
    the model later raises the same error.
    """
    try:
        _warn_on_refused_model_settings(ner_overrides, whisper_model)
    except Exception as exc:
        logger.warning("Model settings check failed: %s", type(exc).__name__)
    try:
        from septum_core.date_grammar import warm_up as warm_up_date_grammar
        warm_up_date_grammar()
    except Exception as exc:
        logger.warning("Date grammar warmup failed: %s", type(exc).__name__)
    try:
        from septum_core.detector import _get_shared_nlp_engine
        _get_shared_nlp_engine()

        from .services.ner_model_registry import get_shared_ner_registry
        get_shared_ner_registry().get_pipeline("en")

        from .services.vector_store import get_shared_sentence_transformer
        model = get_shared_sentence_transformer()
        model.encode(["warmup"], convert_to_numpy=True)
    except Exception as exc:
        reason = exc.code if isinstance(exc, ModelRefusal) else type(exc).__name__
        logger.warning("Model warmup failed: %s", reason)


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


async def _stored_model_settings() -> tuple[dict[str, str] | None, str | None]:
    """Return the stored NER overrides and Whisper model; ``(None, None)`` if unreadable."""
    try:
        async with get_session_maker()() as db:
            row = (await db.execute(select(AppSettings))).scalars().first()
    except Exception as exc:
        logger.warning("Reading model settings for warmup failed: %s", type(exc).__name__)
        return None, None
    if row is None:
        return None, None
    return row.ner_model_overrides, row.whisper_model


async def _announce_setup_token() -> None:
    """Print the setup token to stdout while setup is open; drop a stale one once it is closed.

    Setup counts as open only when :func:`setup_window_open` establishes it:
    either the infrastructure step has not run (no database is configured, so
    no user can exist), or the configured database has no user and
    ``setup_completed`` is false. Anything else, including an upgraded install
    with users, is closed: no token is generated, nothing is printed, and a
    leftover stored token is removed. A database error propagates instead of
    guessing a state.

    The token goes to stdout via ``print`` and never through ``logging``, so
    it stays out of structured log sinks and the ErrorLog table.
    """
    if not await setup_window_open():
        bootstrap.clear_setup_token()
        return

    if bootstrap.setup_token_from_env():
        print(
            f"Septum setup is open; use the token set in {bootstrap.SETUP_TOKEN_ENV}.",
            flush=True,
        )
        return

    token = bootstrap.ensure_setup_token()
    print(f"Septum setup token: {token}", flush=True)
    print(
        "Enter it in the setup wizard; it is deleted when the first admin is created.",
        flush=True,
    )


ERROR_LOG_PURGE_INTERVAL_SECONDS = 24 * 60 * 60

ERROR_LOG_RETENTION_FAILURES = Counter(
    "septum_error_log_retention_failures_total",
    "Error log retention runs that failed (retried on the next tick)",
)


async def _purge_error_logs_once() -> None:
    """Apply the error log retention once; a no-op until the database is set up."""
    if not engine_is_ready():
        return
    async with get_session_maker()() as db:
        removed = await purge_expired_error_logs(
            db, bootstrap.get_config().error_log_retention_days
        )
    if removed:
        logger.info("Purged %d error log entries past retention", removed)


async def _error_log_retention_loop() -> None:
    """Purge expired error logs at startup and then once a day; warn and stop if disabled.

    A failed run is logged (class name only) and retried on the next
    tick, so a transient database error does not end retention for the
    life of the process.
    """
    if bootstrap.get_config().error_log_retention_days < 1:
        logger.warning(
            "Error log retention is disabled (error_log_retention_days < 1); "
            "error log rows are kept indefinitely"
        )
        return
    while True:
        try:
            await _purge_error_logs_once()
        except Exception as exc:
            logger.warning("Error log retention purge failed: %s", type(exc).__name__)
            ERROR_LOG_RETENTION_FAILURES.inc()
        await asyncio.sleep(ERROR_LOG_PURGE_INTERVAL_SECONDS)


async def _install_gateway_client(gateway_forced: bool) -> Any | None:
    """Install the process-wide gateway client when a queue is configured.

    Without ``SEPTUM_QUEUE_URL`` / ``SEPTUM_QUEUE_DIR`` nothing is built, and
    ``USE_GATEWAY_DEFAULT=true`` refuses startup: the router forces gateway
    routing under that flag, with no way to reach the gateway. Databases
    seeded with ``use_gateway`` are refused per request by the router instead.
    """
    if not any(os.getenv(name) for name in QUEUE_ENV_VARS):
        if gateway_forced:
            raise GatewayStartupError(
                "USE_GATEWAY_DEFAULT is true but neither SEPTUM_QUEUE_URL nor "
                "SEPTUM_QUEUE_DIR is set; configure the gateway queue or unset "
                "USE_GATEWAY_DEFAULT."
            )
        return None
    from .services.gateway_client import build_gateway_client_from_env

    client = await build_gateway_client_from_env()

    def _shared_client(_settings: AppSettings) -> GatewayClientProtocol:
        return client

    set_gateway_client_factory(_shared_client)
    logger.info("Gateway client installed")
    return client


@asynccontextmanager
async def _gateway_client_installed(gateway_forced: bool):
    """Hold the gateway client for the lifespan; clear and close it on exit, even after a later failure."""
    gateway_client = await _install_gateway_client(gateway_forced)
    try:
        yield
    finally:
        if gateway_client is not None:
            set_gateway_client_factory(None)
            await gateway_client.aclose()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan handler — initialise database if already configured."""
    from .services.auth import (
        run_password_work,
        shutdown_password_executor,
        warm_dummy_password_hash,
    )

    bootstrap.validate_setup_token_env()
    flags = parse_bool_default_flags()
    async with _gateway_client_installed(flags["USE_GATEWAY_DEFAULT"]):
        await run_password_work(warm_dummy_password_hash)
        config = bootstrap.get_config()
        if not bootstrap.needs_setup():
            url = build_database_url(config.database_url, config.db_path)
            initialize_engine(url)
            await init_db()
            await _reset_orphaned_processing_docs()
            sweep_stale_temp_files()
            if os.getenv(MODEL_WARMUP_OPT_OUT_ENV) == "1":
                logger.info("Model warmup disabled by %s", MODEL_WARMUP_OPT_OUT_ENV)
            else:
                threading.Thread(
                    target=_preload_models,
                    args=await _stored_model_settings(),
                    name="septum-model-warmup",
                    daemon=True,
                ).start()
        await _announce_setup_token()
        retention = asyncio.create_task(_error_log_retention_loop(), name="septum-error-log-retention")
        try:
            yield
        finally:
            retention.cancel()
            with suppress(asyncio.CancelledError):
                await retention
            shutdown_password_executor()


from .middleware.rate_limit import (
    DefaultRateLimit,
    RateLimited,
    get_limiter,
    rate_limited_handler,
)
from .services.auth import PASSWORD_BUSY_RETRY_AFTER_SECONDS, PasswordWorkBusy

limiter = get_limiter()

def _app_version() -> str:
    """Return the app version from the nearest VERSION file; ``0.0.0-dev`` if absent (matches the frontend placeholder)."""
    from pathlib import Path
    for parent in Path(__file__).resolve().parents:
        try:
            return (parent / "VERSION").read_text().strip()
        except (FileNotFoundError, OSError):
            continue
    return "0.0.0-dev"


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


def _custom_openapi() -> dict[str, Any]:
    """Inject the API-key security scheme alongside OAuth2/JWT.

    FastAPI auto-generates the OAuth2 scheme from
    ``OAuth2PasswordBearer`` declared in ``utils/auth_dependency.py``,
    but the API key path lives entirely in ``AuthMiddleware`` and
    therefore stays invisible to the OpenAPI schema by default. This
    override adds ``ApiKeyAuth`` to ``components.securitySchemes`` so
    the Swagger / ReDoc "Authorize" dialog offers both flows.
    """
    from .services.api_key_service import HEADER_NAME, PREFIX, RAW_KEY_HEX_CHARS

    if app.openapi_schema is not None:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    components = schema.setdefault("components", {})
    schemes = components.setdefault("securitySchemes", {})
    schemes["ApiKeyAuth"] = {
        "type": "apiKey",
        "in": "header",
        "name": HEADER_NAME,
        "description": (
            "Long-lived programmatic credential issued via "
            f"`POST /api/api-keys`. Format: `{PREFIX}<{RAW_KEY_HEX_CHARS} hex chars>`."
        ),
    }
    app.openapi_schema = schema
    return schema


app.openapi = _custom_openapi  # type: ignore[method-assign]
app.state.limiter = limiter
app.add_exception_handler(RateLimited, rate_limited_handler)

from .middleware.auth import AuthMiddleware
from .middleware.security_headers import SecurityHeadersMiddleware
from .utils.metrics import PrometheusMiddleware, metrics_endpoint

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(AuthMiddleware)
app.add_middleware(PrometheusMiddleware)

# CORS must be the outermost middleware (added last = wraps everything)
# so that preflight OPTIONS responses always carry the correct headers.
from .utils.cors import resolve_cors_origins  # noqa: E402

_cors_origins = resolve_cors_origins(bootstrap.get_config().frontend_origin)
if _cors_origins == ["*"]:
    logger.warning(
        "CORS allow_origins is wildcard (*). Set FRONTEND_ORIGIN to an "
        "explicit comma-separated list before exposing this instance."
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_route("/metrics", metrics_endpoint, methods=["GET"])


# Every router carries its own /api/... prefix and is mounted once, with no
# extra prefix here: error log rows store the route template, which must be
# the path the route is actually served at.
_all_routers = [
    setup_router.router,
    auth_router.router,
    api_keys_router.router,
    approval_router.router,
    audit_router.router,
    documents_router.router,
    chunks_router.router,
    chat_router.router,
    chat_sessions_router.router,
    settings_router.router,
    error_logs_router.router,
    regulations_router.router,
    relationships_router.router,
    text_normalization_router.router,
    users_router.router,
]

default_rate_limit = DefaultRateLimit(bootstrap.get_config().rate_limit)
for _r in _all_routers:
    app.include_router(_r, dependencies=[Depends(default_rate_limit)])



# Rejections of callers without credentials are counted, not stored: anyone
# can produce them, so persisting each one would let a client grow the
# error log at will. The only label is the status code.
UNAUTHENTICATED_REJECTIONS = Counter(
    "septum_unauthenticated_rejections_total",
    "401/403 answers to requests without a resolved user or API key",
    ["status"],
)
_UNAUTHENTICATED_REJECTION_CODES = frozenset(
    {http_status.HTTP_401_UNAUTHORIZED, http_status.HTTP_403_FORBIDDEN}
)


def _is_unauthenticated_rejection(request: Request, status_code: int) -> bool:
    return (
        status_code in _UNAUTHENTICATED_REJECTION_CODES
        and getattr(request.state, "auth_user_id", None) is None
    )


PASSWORD_WORK_REFUSED = Counter(
    "septum_password_work_refused_total",
    "Login, register and change-password requests refused because the bcrypt queue was full",
)


@app.exception_handler(PasswordWorkBusy)
async def password_work_busy_handler(request: Request, exc: PasswordWorkBusy) -> JSONResponse:
    """503 with Retry-After when the bcrypt queue is full; no error log row, only a counter."""
    PASSWORD_WORK_REFUSED.inc()
    return JSONResponse(
        status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "Server is busy, try again shortly.", "code": "server_busy"},
        headers={"Retry-After": str(PASSWORD_BUSY_RETRY_AFTER_SECONDS)},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(
    request: Request, exc: HTTPException
) -> JSONResponse:
    """Return standard JSON for HTTPException so routes keep correct status codes.

    Also persists an error_log entry for any 4xx/5xx so the Error Logs UI
    surfaces these failures. 404s are excluded because they are noisy
    under healthchecks/probes. 401/403 for a caller with no resolved
    identity are only counted (``septum_unauthenticated_rejections_total``),
    never stored, so anonymous clients cannot grow the table. Every other
    row draws on the caller's error-log budget inside the logger (a
    separate, smaller one for 5xx); past it the failure is only counted
    (``septum_error_log_writes_dropped_total``). Stored rows keep the
    request's user agent and request id, cut to their column widths: they
    are useful for correlating a failure with a client.

    5xx errors are routed through ``log_backend_error`` so the full
    ``traceback.format_exception`` stack is captured — otherwise the
    Error Logs "Detay" view lands on an entry with no stack trace, no
    exception type, and no pointer to the source file that raised, which
    makes repeat 500s impossible to diagnose in production. 4xx entries
    still use ``log_backend_message`` (WARNING level, no stack) because
    they are typically handled validation failures where the stack is
    noise rather than signal.
    """
    code = getattr(exc, "code", None)
    log_message = getattr(exc, "log_message", None) or (
        str(exc.detail) if exc.detail else exc.__class__.__name__
    )
    if _is_unauthenticated_rejection(request, exc.status_code):
        UNAUTHENTICATED_REJECTIONS.labels(status=str(exc.status_code)).inc()
    elif exc.status_code >= 400 and exc.status_code != http_status.HTTP_404_NOT_FOUND:
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
                            message=log_message,
                            level="WARNING",
                            status_code=exc.status_code,
                        )
        except Exception as log_exc:
            # Logging must never break the response; the failure is still reported.
            report_error_log_failure(log_exc)
    content: dict[str, Any] = {"detail": exc.detail}
    if code is not None:
        content["code"] = code
    return JSONResponse(status_code=exc.status_code, content=content)


@app.exception_handler(ModelRefusal)
async def model_refusal_handler(request: Request, exc: ModelRefusal) -> JSONResponse:
    """Turn a refused model load anywhere in a route into 503 with its code.

    The detail is fixed, host-free text; the exception is persisted to the
    error log like any other server-side failure.
    """
    try:
        if engine_is_ready():
            async with get_session_maker()() as db:
                await log_backend_error(
                    db=db,
                    request=request,
                    exc=exc,
                    level="ERROR",
                    status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                )
    except Exception as log_exc:
        report_error_log_failure(log_exc)
    return JSONResponse(
        status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": MODEL_REFUSED_DETAIL, "code": exc.code},
    )


@app.exception_handler(Exception)
async def global_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Global fallback exception handler that also persists an error log entry (within budget)."""
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
    except Exception as log_exc:
        report_error_log_failure(log_exc)

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
        except Exception as exc:
            logger.debug("Health check could not read llm_provider: %s", type(exc).__name__)

    from .services.llm_providers.health import get_all_statuses
    redis_ok = await redis_ping()
    result["redis"] = "connected" if redis_ok else "unavailable"
    result["rate_limit_storage"] = "ok" if limiter.buckets.storage_up else "degraded"
    result["llm_providers"] = get_all_statuses()

    return result

