from __future__ import annotations

"""FastAPI router for managing persisted application settings.

This router exposes a small REST surface around the :class:`AppSettings`
ORM model so that the frontend Settings UI can read and update the global
configuration. All fields are strongly typed and partial updates are
supported via a dedicated PATCH schema.
"""

from typing import Optional
import os

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.settings import AppSettings


router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsResponse(BaseModel):
    """Serialized view of global application settings."""

    model_config = ConfigDict(from_attributes=True)

    id: int

    # LLM configuration
    llm_provider: str
    llm_model: str
    ollama_base_url: str
    ollama_chat_model: str
    ollama_deanon_model: str

    # De-anonymization & approval
    deanon_enabled: bool
    deanon_strategy: str
    require_approval: bool
    show_json_output: bool

    # Sanitization layers
    use_presidio_layer: bool
    use_ner_layer: bool
    use_ollama_layer: bool

    # RAG / chunking
    chunk_size: int
    chunk_overlap: int
    top_k_retrieval: int
    pdf_chunk_size: int
    audio_chunk_size: int
    spreadsheet_chunk_size: int

    # Ingestion / models
    whisper_model: str
    image_ocr_languages: list[str]
    extract_embedded_images: bool
    recursive_email_attachments: bool

    # Regulations
    default_active_regulations: list[str]


class SettingsUpdatePayload(BaseModel):
    """PATCH payload for partially updating application settings."""

    # LLM configuration
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    ollama_base_url: Optional[str] = None
    ollama_chat_model: Optional[str] = None
    ollama_deanon_model: Optional[str] = None

    # De-anonymization & approval
    deanon_enabled: Optional[bool] = None
    deanon_strategy: Optional[str] = None
    require_approval: Optional[bool] = None
    show_json_output: Optional[bool] = None

    # Sanitization layers
    use_presidio_layer: Optional[bool] = None
    use_ner_layer: Optional[bool] = None
    use_ollama_layer: Optional[bool] = None

    # RAG / chunking
    chunk_size: Optional[int] = None
    chunk_overlap: Optional[int] = None
    top_k_retrieval: Optional[int] = None
    pdf_chunk_size: Optional[int] = None
    audio_chunk_size: Optional[int] = None
    spreadsheet_chunk_size: Optional[int] = None

    # Ingestion / models
    whisper_model: Optional[str] = None
    image_ocr_languages: Optional[list[str]] = None
    extract_embedded_images: Optional[bool] = None
    recursive_email_attachments: Optional[bool] = None

    # Regulations
    default_active_regulations: Optional[list[str]] = None


async def _load_settings(session: AsyncSession) -> AppSettings:
    """Return the singleton :class:`AppSettings` row or raise HTTP 500."""
    result = await session.execute(select(AppSettings).where(AppSettings.id == 1))
    settings = result.scalar_one_or_none()
    if settings is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Application settings have not been initialized.",
        )

    # On startup or first access, allow certain fields to be overridden by
    # environment variables and persist those overrides back to the database.
    # This keeps .env as the single source of truth for defaults while still
    # exposing a consistent view via the REST API and ORM.
    env_llm_model = os.getenv("LLM_MODEL")
    env_llm_provider = os.getenv("LLM_PROVIDER")
    changed = False

    if env_llm_model and env_llm_model != settings.llm_model:
        settings.llm_model = env_llm_model
        changed = True

    if env_llm_provider and env_llm_provider != settings.llm_provider:
        settings.llm_provider = env_llm_provider
        changed = True

    if changed:
        await session.commit()
        await session.refresh(settings)

    return settings


@router.get(
    "",
    response_model=SettingsResponse,
    status_code=status.HTTP_200_OK,
)
async def get_settings_endpoint(
    db: AsyncSession = Depends(get_db),
) -> SettingsResponse:
    """Return the current global application settings."""
    settings = await _load_settings(db)
    return SettingsResponse.model_validate(settings)


@router.patch(
    "",
    response_model=SettingsResponse,
    status_code=status.HTTP_200_OK,
)
async def update_settings_endpoint(
    payload: SettingsUpdatePayload,
    db: AsyncSession = Depends(get_db),
) -> SettingsResponse:
    """Partially update global application settings."""
    settings = await _load_settings(db)

    update_data = payload.model_dump(exclude_unset=True)
    for field_name, value in update_data.items():
        setattr(settings, field_name, value)

    await db.commit()
    await db.refresh(settings)

    return SettingsResponse.model_validate(settings)

