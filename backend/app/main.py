from __future__ import annotations

"""FastAPI application entrypoint for Septum."""

from contextlib import asynccontextmanager
import os

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db, init_db
from .models.settings import AppSettings
from .utils.device import get_device


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan handler to initialize the database on startup."""
    await init_db()
    yield


app = FastAPI(title="Septum API", lifespan=lifespan)


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

    return {
        "status": "ok",
        "device": get_device(),
        "llm_provider": llm_provider,
    }

