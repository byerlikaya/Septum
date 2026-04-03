from __future__ import annotations

"""FastAPI application entrypoint for Septum."""

import logging
import warnings

warnings.filterwarnings(
    "ignore",
    message=".*has conflict with protected namespace.*model_.*",
    category=UserWarning,
)

logging.getLogger("presidio_analyzer.recognizer_registry").setLevel(logging.ERROR)
logging.getLogger("paddlex").setLevel(logging.ERROR)
logging.getLogger("paddleocr").setLevel(logging.ERROR)

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
from .routers import approval as approval_router
from .routers import audit as audit_router
from .routers import chat as chat_router
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


app = FastAPI(title="Septum API", lifespan=lifespan)

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


app.include_router(approval_router.router)
app.include_router(audit_router.router)
app.include_router(documents_router.router)
app.include_router(chunks_router.router)
app.include_router(chat_router.router)
app.include_router(settings_router.router)
app.include_router(error_logs_router.router)
app.include_router(regulations_router.router)
app.include_router(text_normalization_router.router)


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

