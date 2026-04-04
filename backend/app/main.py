from __future__ import annotations

"""FastAPI application entrypoint for Septum."""

import logging
import os as _os
import warnings

warnings.filterwarnings(
    "ignore",
    message=".*has conflict with protected namespace.*model_.*",
    category=UserWarning,
)

from .utils.logging_config import setup_structured_logging

setup_structured_logging(_os.getenv("LOG_LEVEL", "INFO"))

import asyncio
from contextlib import asynccontextmanager
import os
import threading

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette import status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import async_session_maker, get_db, init_db
from .models.settings import AppSettings
from .routers import auth as auth_router
from .routers import approval as approval_router
from .routers import audit as audit_router
from .routers import chat as chat_router
from .routers import chat_sessions as chat_sessions_router
from .routers import chunks as chunks_router
from .routers import documents as documents_router
from .routers import error_logs as error_logs_router
from .routers import regulations as regulations_router
from .routers import settings as settings_router
from .routers import text_normalization as text_normalization_router
from .services.error_logger import log_backend_error
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan handler to initialize the database on startup."""
    await init_db()
    threading.Thread(target=_preload_models, daemon=True).start()
    yield


from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

_rate_limit_default = os.getenv("RATE_LIMIT_DEFAULT", "60/minute")
_redis_url = os.getenv("REDIS_URL", "")
_limiter_storage = f"redis://{_redis_url.split('://')[-1]}" if _redis_url else "memory://"

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[_rate_limit_default],
    storage_uri=_limiter_storage,
)

app = FastAPI(title="Septum API", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

frontend_origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")
_cors_origins = [
    origin.strip()
    for origin in frontend_origin.split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from .utils.metrics import PrometheusMiddleware, metrics_endpoint

app.add_middleware(PrometheusMiddleware)
app.add_route("/metrics", metrics_endpoint, methods=["GET"])


# All API routers are mounted under both /api (legacy) and /api/v1 (versioned).
_all_routers = [
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
]

for _r in _all_routers:
    app.include_router(_r)



@app.exception_handler(HTTPException)
async def http_exception_handler(
    _request: Request, exc: HTTPException
) -> JSONResponse:
    """Return standard JSON for HTTPException so routes keep correct status codes."""
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
        async with async_session_maker() as db:
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
async def health(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    """Health check endpoint that reports backend status and configuration."""
    result = await db.execute(select(AppSettings).where(AppSettings.id == 1))
    settings = result.scalar_one_or_none()

    llm_provider = (
        settings.llm_provider
        if settings is not None
        else os.getenv("LLM_PROVIDER", "anthropic")
    )

    from .services.redis_client import redis_ping
    from .services.llm_providers.health import get_all_statuses

    redis_ok = await redis_ping()

    return {
        "status": "ok",
        "device": get_device(),
        "llm_provider": llm_provider,
        "redis": "connected" if redis_ok else "unavailable",
        "llm_providers": get_all_statuses(),
    }

