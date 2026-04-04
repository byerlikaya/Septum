from __future__ import annotations

"""FastAPI router for managing persisted application settings.

This router exposes a small REST surface around the :class:`AppSettings`
ORM model so that the frontend Settings UI can read and update the global
configuration. All fields are strongly typed and partial updates are
supported via a dedicated PATCH schema.
"""

from typing import Any, Optional
import os
import asyncio
import shutil
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.settings import AppSettings
from ..services.error_logger import log_backend_error, log_backend_message
from ..services.llm_router import LLMRouter, LLMRouterError
from ..utils.db_helpers import load_settings
from ..utils.device import get_device


router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsResponse(BaseModel):
    """Serialized view of global application settings."""

    model_config = ConfigDict(from_attributes=True)

    id: int

    llm_provider: str
    llm_model: str
    ollama_base_url: str
    ollama_chat_model: str
    ollama_deanon_model: str

    deanon_enabled: bool
    deanon_strategy: str
    require_approval: bool
    show_json_output: bool

    use_presidio_layer: bool
    use_ner_layer: bool
    use_ollama_validation_layer: bool
    use_ollama_layer: bool

    chunk_size: int
    chunk_overlap: int
    top_k_retrieval: int
    pdf_chunk_size: int
    audio_chunk_size: int
    spreadsheet_chunk_size: int

    whisper_model: str
    default_audio_language: Optional[str] = None
    image_ocr_languages: list[str]
    ocr_provider: str
    ocr_provider_options: Optional[dict] = None
    extract_embedded_images: bool
    recursive_email_attachments: bool

    default_active_regulations: list[str]
    ner_model_overrides: Optional[dict[str, str]] = None

    setup_completed: bool


class SettingsUpdatePayload(BaseModel):
    """PATCH payload for partially updating application settings."""

    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    ollama_base_url: Optional[str] = None
    ollama_chat_model: Optional[str] = None
    ollama_deanon_model: Optional[str] = None

    deanon_enabled: Optional[bool] = None
    deanon_strategy: Optional[str] = None
    require_approval: Optional[bool] = None
    show_json_output: Optional[bool] = None

    use_presidio_layer: Optional[bool] = None
    use_ner_layer: Optional[bool] = None
    use_ollama_validation_layer: Optional[bool] = None
    use_ollama_layer: Optional[bool] = None

    chunk_size: Optional[int] = None
    chunk_overlap: Optional[int] = None
    top_k_retrieval: Optional[int] = None
    pdf_chunk_size: Optional[int] = None
    audio_chunk_size: Optional[int] = None
    spreadsheet_chunk_size: Optional[int] = None

    whisper_model: Optional[str] = None
    default_audio_language: Optional[str] = None
    image_ocr_languages: Optional[list[str]] = None
    ocr_provider: Optional[str] = None
    ocr_provider_options: Optional[dict[str, Any]] = None
    extract_embedded_images: Optional[bool] = None
    recursive_email_attachments: Optional[bool] = None

    default_active_regulations: Optional[list[str]] = None
    ner_model_overrides: Optional[dict[str, str]] = None

    setup_completed: Optional[bool] = None


class TestLLMRequest(BaseModel):
    """Request body for testing the configured cloud LLM connection."""

    provider: Optional[str] = None
    model: Optional[str] = None


class TestLocalModelsRequest(BaseModel):
    """Request body for testing connectivity to the local model server."""

    base_url: Optional[str] = None


class TestConnectionResponse(BaseModel):
    """Simple success/error envelope for connection test endpoints."""

    ok: bool
    message: Optional[str] = None


class AudioPipelineHealthResponse(BaseModel):
    """Health information for the local audio ingestion pipeline."""

    ffmpeg: str
    whisper_package: str
    whisper_model: str
    message: Optional[str] = None


class WhisperInstallResponse(BaseModel):
    """Result envelope for installing or downloading a Whisper model."""

    status: str
    message: Optional[str] = None


@router.get(
    "",
    response_model=SettingsResponse,
    status_code=status.HTTP_200_OK,
)
async def get_settings_endpoint(
    db: AsyncSession = Depends(get_db),
) -> SettingsResponse:
    """Return the current global application settings."""
    settings = await load_settings(db)
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
    settings = await load_settings(db)

    update_data = payload.model_dump(exclude_unset=True)
    for field_name, value in update_data.items():
        setattr(settings, field_name, value)

    await db.commit()
    await db.refresh(settings)

    return SettingsResponse.model_validate(settings)


@router.post(
    "/test-llm",
    response_model=TestConnectionResponse,
    status_code=status.HTTP_200_OK,
)
async def test_llm_connection_endpoint(
    payload: TestLLMRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TestConnectionResponse:
    """Validate connectivity to the configured cloud LLM provider.

    This endpoint performs a minimal completion call using :class:`LLMRouter`
    and reports whether the provider is reachable and correctly configured.
    No user PII is ever sent – the prompt is a fixed, non-sensitive string.
    """
    settings = await load_settings(db)

    if payload.provider is not None:
        settings.llm_provider = payload.provider
    if payload.model is not None:
        settings.llm_model = payload.model

    used_fallback: list[bool] = [False]

    async def on_cloud_failure(message: str, extra: dict[str, Any]) -> None:
        used_fallback[0] = True
        await log_backend_message(db, request, message, level="ERROR", extra=extra)

    router = LLMRouter(settings)
    try:
        await router.complete(
            messages=[
                {
                    "role": "user",
                    "content": "Connection test from Septum settings UI.",
                }
            ],
            max_tokens=8,
            on_cloud_failure=on_cloud_failure,
        )
    except LLMRouterError as exc:
        try:
            await log_backend_error(db, request, exc, status_code=400)
        except Exception:  # noqa: BLE001
            pass
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:  # noqa: BLE001
        try:
            await log_backend_error(db, request, exc, status_code=400)
        except Exception:  # noqa: BLE001
            pass
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cloud LLM connection test failed due to an unexpected error.",
        ) from exc

    if used_fallback[0]:
        return TestConnectionResponse(
            ok=False,
            message=(
                "Cloud LLM connection failed (e.g. invalid API key or unreachable provider). "
                "Local Ollama fallback responded, but the cloud provider is not correctly configured."
            ),
        )

    return TestConnectionResponse(
        ok=True,
        message="Cloud LLM connection test succeeded.",
    )


@router.post(
    "/test-local-models",
    response_model=TestConnectionResponse,
    status_code=status.HTTP_200_OK,
)
async def test_local_models_endpoint(
    payload: TestLocalModelsRequest,
    db: AsyncSession = Depends(get_db),
) -> TestConnectionResponse:
    """Check that the local model server (for example Ollama) is reachable.

    The check is intentionally lightweight and only verifies that the HTTP
    endpoint responds successfully; it does not load or run any specific
    model to avoid unnecessary resource usage.
    """
    settings = await load_settings(db)

    base_url = (payload.base_url or settings.ollama_base_url or "").rstrip("/")
    if not base_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OLLAMA_BASE_URL is not configured.",
        )

    url = f"{base_url}/api/tags"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Local model server is not reachable. "
                "Please ensure Ollama is running and the base URL is correct."
            ),
        ) from exc

    return TestConnectionResponse(
        ok=True,
        message="Local model server is reachable.",
    )


@router.get(
    "/ingestion/health",
    response_model=AudioPipelineHealthResponse,
    status_code=status.HTTP_200_OK,
)
async def get_ingestion_health_endpoint(
    db: AsyncSession = Depends(get_db),
) -> AudioPipelineHealthResponse:
    """Return health information for the local ingestion pipeline.

    This endpoint performs lightweight checks only – it verifies that:

    * The ``ffmpeg`` binary is available on PATH.
    * The ``openai-whisper`` Python package is importable.
    * The configured Whisper model appears to be downloaded in the cache
      directory (best-effort check).
    """
    settings = await load_settings(db)

    ffmpeg_status = "ok" if shutil.which("ffmpeg") is not None else "missing"

    whisper_package_status = "ok"
    whisper_model_status = "unknown"
    messages: list[str] = []

    try:
        import whisper  # type: ignore[import]
    except ImportError:
        whisper_package_status = "missing"
        whisper_model_status = "missing"
        messages.append(
            "Python package 'openai-whisper' is not installed in the backend environment."
        )
    else:
        model_name = (settings.whisper_model or "base").strip()
        xdg_cache_home = os.getenv("XDG_CACHE_HOME")
        base_cache_dir = (
            Path(xdg_cache_home)
            if xdg_cache_home
            else Path(os.path.expanduser("~")) / ".cache"
        )
        try:
            import whisper  # type: ignore[import]

            models_map = getattr(whisper, "_MODELS", {})  # type: ignore[attr-defined]
            model_url = models_map.get(model_name)
            if isinstance(model_url, str) and model_url:
                filename = os.path.basename(model_url)
            else:
                filename = f"{model_name}.pt"
        except Exception:  # noqa: BLE001
            filename = f"{model_name}.pt"

        cache_dir = base_cache_dir / "whisper"
        model_path = cache_dir / filename
        if model_path.exists():
            whisper_model_status = "ok"
        else:
            whisper_model_status = "missing"
            messages.append(
                f"Whisper model '{model_name}' has not been downloaded yet "
                f"(expected at '{model_path}')."
            )

    if ffmpeg_status == "missing":
        messages.append("The 'ffmpeg' binary was not found on PATH.")

    message = " ".join(messages) if messages else None

    return AudioPipelineHealthResponse(
        ffmpeg=ffmpeg_status,
        whisper_package=whisper_package_status,
        whisper_model=whisper_model_status,
        message=message,
    )


@router.post(
    "/ingestion/install-whisper-model",
    response_model=WhisperInstallResponse,
    status_code=status.HTTP_200_OK,
)
async def install_whisper_model_endpoint(
    db: AsyncSession = Depends(get_db),
) -> WhisperInstallResponse:
    """Download and cache the configured Whisper model.

    This endpoint loads the Whisper model specified in settings using the local
    device (CPU, CUDA, or MPS). If the model is not present, it will be
    downloaded to the standard Whisper cache directory.
    """
    settings = await load_settings(db)
    model_name = (settings.whisper_model or "base").strip()

    try:
        import whisper  # type: ignore[import]
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Python package 'openai-whisper' is not installed. "
                "Install it in the backend environment to enable audio transcription."
            ),
        ) from exc

    def _load_model_sync() -> None:
        device = get_device()
        whisper.load_model(model_name, device=device)

    try:
        await asyncio.to_thread(_load_model_sync)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to download or load Whisper model '{model_name}': {exc}",
        ) from exc

    return WhisperInstallResponse(
        status="ok",
        message=f"Whisper model '{model_name}' is available for ingestion.",
    )

