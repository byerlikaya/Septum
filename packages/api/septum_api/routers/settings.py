from __future__ import annotations

"""FastAPI router for managing persisted application settings.

This router exposes a small REST surface around the :class:`AppSettings`
ORM model so that the frontend Settings UI can read and update the global
configuration. All fields are strongly typed and partial updates are
supported via a dedicated PATCH schema.
"""

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from septum_core.model_integrity import (
    UnpinnedModelError,
    parse_model_spec,
    split_model_specs,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..errors import CodedHTTPError, public_error_text
from ..models.user import User
from ..request_limits import MAX_LLM_MODEL_LENGTH, MAX_LLM_PROVIDER_LENGTH
from ..services.error_logger import log_backend_message, report_error_log_failure
from ..services.llm_errors import LLM_UNAVAILABLE, OLLAMA_URL_NOT_ALLOWED_CODE
from ..services.llm_router import LLMRouter, LLMRouterError
from ..services.ollama_client import (
    OLLAMA_URL_REFUSED_MESSAGE,
    effective_ollama_base_url,
    ollama_async_http_client,
    ollama_url_problem,
    ollama_url_refused,
)
from ..services.whisper_models import (
    WhisperInstallBusy,
    ensure_whisper_model,
    require_registry_model_http,
)
from ..utils.auth_dependency import (
    get_current_user,
    require_admin_or_bootstrap,
    require_interactive_session,
    require_role,
)
from ..utils.db_helpers import load_settings
from ..utils.device import get_device

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])

LLM_TEST_UNAVAILABLE_MESSAGE = (
    "The LLM provider could not be reached; check the error logs for details."
)


async def _validate_regulation_ids(
    db: AsyncSession,
    ids: Optional[list[str]],
) -> None:
    """Raise 422 if ``ids`` contains any id that is neither a built-in pack
    nor an existing custom ``RegulationRuleset`` row.

    This catches client-side typos (``"gdrp"`` instead of ``"gdpr"``) that
    would otherwise be stored silently and later composed into an empty
    policy — the user thinks a regulation is active when it is not.
    """
    from septum_core.recognizers import BUILTIN_REGULATION_IDS
    from sqlalchemy import select

    from ..models.regulation import RegulationRuleset

    if not ids:
        return
    candidate = [rid.lower() for rid in ids if rid]
    known: set[str] = {str(x) for x in BUILTIN_REGULATION_IDS}
    unknown = [rid for rid in candidate if rid not in known]
    if unknown:
        rows = await db.execute(
            select(RegulationRuleset.id).where(RegulationRuleset.id.in_(unknown))
        )
        known.update(row[0] for row in rows.all())
    still_unknown = sorted(set(candidate) - known)
    if still_unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown regulation id(s): {still_unknown}",
        )


def _validate_ollama_url(url: str) -> str:
    """Validate an Ollama URL against :func:`ollama_url_problem`.

    Raises :class:`CodedHTTPError` (HTTP 400, ``ollama_url_not_allowed``)
    with the reason when the URL is refused. The detail may echo the host,
    so the persisted log message is the host-free refusal text.
    """
    problem = ollama_url_problem(url)
    if problem is not None:
        raise CodedHTTPError(
            status.HTTP_400_BAD_REQUEST,
            problem,
            OLLAMA_URL_NOT_ALLOWED_CODE,
            log_message=OLLAMA_URL_REFUSED_MESSAGE,
        )
    return url


def _validate_ner_overrides(overrides: dict[str, str] | None) -> None:
    """Refuse NER overrides that name an unpinned model.

    Each comma-separated id must be a manifest model or
    ``org/name@<40-hex commit>``; otherwise raises :class:`CodedHTTPError`
    (HTTP 400, ``ner_model_unpinned``) whose detail never echoes the value.
    """
    try:
        for spec in split_model_specs(overrides):
            parse_model_spec(spec)
    except UnpinnedModelError as exc:
        raise CodedHTTPError(status.HTTP_400_BAD_REQUEST, str(exc), exc.code) from None


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
    require_approval: bool
    approval_timeout_seconds: int
    show_json_output: bool

    use_presidio_layer: bool
    use_ner_layer: bool
    use_ollama_validation_layer: bool
    use_ollama_layer: bool
    use_ollama_semantic_layer: bool

    chunk_size: int
    chunk_overlap: int
    top_k_retrieval: int
    pdf_chunk_size: int
    audio_chunk_size: int
    spreadsheet_chunk_size: int
    rag_relevance_threshold: float

    whisper_model: str
    default_audio_language: Optional[str] = None
    image_ocr_languages: list[str]
    ocr_provider: str
    ocr_provider_options: Optional[dict] = None

    default_active_regulations: list[str]
    ner_model_overrides: Optional[dict[str, str]] = None

    has_anthropic_key: bool = False
    has_openai_key: bool = False
    has_openrouter_key: bool = False
    ollama_url_not_allowed: bool

    setup_completed: bool


class SettingsUpdatePayload(BaseModel):
    """PATCH payload for partially updating application settings."""

    llm_provider: Optional[str] = Field(default=None, max_length=MAX_LLM_PROVIDER_LENGTH)
    llm_model: Optional[str] = Field(default=None, max_length=MAX_LLM_MODEL_LENGTH)
    ollama_base_url: Optional[str] = None
    ollama_chat_model: Optional[str] = None
    ollama_deanon_model: Optional[str] = None

    deanon_enabled: Optional[bool] = None
    require_approval: Optional[bool] = None
    approval_timeout_seconds: Optional[int] = None
    show_json_output: Optional[bool] = None

    use_presidio_layer: Optional[bool] = None
    use_ner_layer: Optional[bool] = None
    use_ollama_validation_layer: Optional[bool] = None
    use_ollama_layer: Optional[bool] = None
    use_ollama_semantic_layer: Optional[bool] = None

    chunk_size: Optional[int] = None
    chunk_overlap: Optional[int] = None
    top_k_retrieval: Optional[int] = None
    pdf_chunk_size: Optional[int] = None
    audio_chunk_size: Optional[int] = None
    spreadsheet_chunk_size: Optional[int] = None
    rag_relevance_threshold: Optional[float] = None

    whisper_model: Optional[str] = None
    default_audio_language: Optional[str] = None
    image_ocr_languages: Optional[list[str]] = None
    ocr_provider: Optional[str] = None
    ocr_provider_options: Optional[dict[str, Any]] = None

    default_active_regulations: Optional[list[str]] = None
    ner_model_overrides: Optional[dict[str, str]] = None

    anthropic_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None

    setup_completed: Optional[bool] = None


class TestLLMRequest(BaseModel):
    """Request body for testing the configured cloud LLM connection."""

    provider: Optional[str] = Field(default=None, max_length=MAX_LLM_PROVIDER_LENGTH)
    model: Optional[str] = Field(default=None, max_length=MAX_LLM_MODEL_LENGTH)


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
    _user: User = Depends(get_current_user),
) -> SettingsResponse:
    """Return the current global application settings."""
    settings = await load_settings(db)
    data = {c.key: getattr(settings, c.key) for c in settings.__table__.columns}
    data["has_anthropic_key"] = bool(settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY"))
    data["has_openai_key"] = bool(settings.openai_api_key or os.getenv("OPENAI_API_KEY"))
    data["has_openrouter_key"] = bool(settings.openrouter_api_key or os.getenv("OPENROUTER_API_KEY"))
    data["ollama_url_not_allowed"] = ollama_url_refused(settings.ollama_base_url)
    return SettingsResponse(**data)


@router.patch(
    "",
    response_model=SettingsResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_interactive_session)],
)
async def update_settings_endpoint(
    payload: SettingsUpdatePayload,
    db: AsyncSession = Depends(get_db),
    _user: User | None = Depends(require_admin_or_bootstrap),
) -> SettingsResponse:
    """Partially update global application settings.

    Admin-only once the wizard has completed. During the first-run
    bootstrap window (``users`` table empty and ``setup_completed``
    still ``False``) the setup wizard can PATCH this endpoint
    anonymously so it can persist LLM / infra choices before the
    admin account is created.
    """
    settings = await load_settings(db)

    update_data = payload.model_dump(exclude_unset=True)

    if "default_active_regulations" in update_data:
        await _validate_regulation_ids(db, update_data["default_active_regulations"])
    if update_data.get("ollama_base_url") is not None:
        _validate_ollama_url(update_data["ollama_base_url"])
    if "ner_model_overrides" in update_data:
        _validate_ner_overrides(update_data["ner_model_overrides"])
    if "whisper_model" in update_data and update_data["whisper_model"] is None:
        del update_data["whisper_model"]
    if "whisper_model" in update_data:
        await require_registry_model_http(update_data["whisper_model"])

    for field_name, value in update_data.items():
        setattr(settings, field_name, value)

    await db.commit()
    await db.refresh(settings)

    data = {c.key: getattr(settings, c.key) for c in settings.__table__.columns}
    data["has_anthropic_key"] = bool(settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY"))
    data["has_openai_key"] = bool(settings.openai_api_key or os.getenv("OPENAI_API_KEY"))
    data["has_openrouter_key"] = bool(settings.openrouter_api_key or os.getenv("OPENROUTER_API_KEY"))
    data["ollama_url_not_allowed"] = ollama_url_refused(settings.ollama_base_url)
    return SettingsResponse(**data)


@router.post(
    "/test-llm",
    response_model=TestConnectionResponse,
    status_code=status.HTTP_200_OK,
)
async def test_llm_connection_endpoint(
    payload: TestLLMRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user: User | None = Depends(require_admin_or_bootstrap),
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
        try:
            await log_backend_message(db, request, message, level="ERROR", extra=extra)
        except Exception as log_exc:
            report_error_log_failure(log_exc)

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
        # The exception handler records the resulting 400; no second row here.
        if exc.code is not None:
            raise CodedHTTPError(
                status.HTTP_400_BAD_REQUEST, str(exc), exc.code
            ) from exc
        raise CodedHTTPError(
            status.HTTP_400_BAD_REQUEST, LLM_TEST_UNAVAILABLE_MESSAGE, LLM_UNAVAILABLE
        ) from exc
    except Exception as exc:
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
    _user: User | None = Depends(require_admin_or_bootstrap),
) -> TestConnectionResponse:
    """Check that the local model server (for example Ollama) is reachable.

    The check is intentionally lightweight and only verifies that the HTTP
    endpoint responds successfully; it does not load or run any specific
    model to avoid unnecessary resource usage.
    """
    settings = await load_settings(db)

    configured = payload.base_url or settings.ollama_base_url
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OLLAMA_BASE_URL is not configured.",
        )
    base_url = effective_ollama_base_url(configured)
    _validate_ollama_url(base_url)

    url = f"{base_url}/api/tags"

    try:
        async with ollama_async_http_client(5.0) as client:
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

    # Check if the configured model is available.
    models_data = response.json()
    available = [m.get("name", "") for m in models_data.get("models", [])]
    chat_model = settings.ollama_chat_model or ""
    model_found = any(chat_model == m or chat_model == m.split(":")[0] for m in available)

    if not model_found and chat_model:
        return TestConnectionResponse(
            ok=False,
            message=f"Ollama is running but model '{chat_model}' is not installed. Pull it first.",
        )

    return TestConnectionResponse(
        ok=True,
        message="Local model server is reachable and model is available.",
    )


class OllamaProbeResponse(BaseModel):
    """Result of probing multiple Ollama URLs to find a reachable instance.

    ``default_url`` is the server's configured Ollama base URL (the value the
    settings seed uses), or ``None`` when that value fails the local-host check.
    It is returned whether or not a probe target answers.
    """

    url: Optional[str] = None
    models: list[str] = []
    default_url: Optional[str] = None


def _configured_ollama_default() -> Optional[str]:
    """Return the configured default Ollama base URL if it passes validation, else ``None``."""
    url = effective_ollama_base_url(None)
    return None if ollama_url_problem(url) is not None else url


_OLLAMA_PROBE_URLS = [
    "http://host.docker.internal:11434",
    "http://ollama:11434",
    "http://localhost:11434",
]


@router.get(
    "/ollama-probe",
    response_model=OllamaProbeResponse,
    status_code=status.HTTP_200_OK,
)
async def ollama_probe_endpoint(
    db: AsyncSession = Depends(get_db),
    _user: User | None = Depends(require_admin_or_bootstrap),
) -> OllamaProbeResponse:
    """Probe common Ollama URLs and return the first reachable one.

    Tries ``host.docker.internal`` (Docker → host), ``ollama`` (Compose
    service), and ``localhost`` in order. Returns the first URL that
    responds to ``/api/tags`` along with the available model names, plus
    the server's configured default URL in ``default_url``.
    """
    default_url = _configured_ollama_default()
    async with ollama_async_http_client(2.0) as client:
        for base in _OLLAMA_PROBE_URLS:
            try:
                resp = await client.get(f"{base}/api/tags")
                resp.raise_for_status()
                names = [m.get("name", "") for m in resp.json().get("models", [])]
                return OllamaProbeResponse(url=base, models=names, default_url=default_url)
            except httpx.HTTPError:
                continue
    return OllamaProbeResponse(default_url=default_url)


class OllamaModelInfo(BaseModel):
    """Single Ollama model entry returned by the listing endpoint."""

    name: str
    size: str
    parameter_size: Optional[str] = None
    quantization: Optional[str] = None


class OllamaModelsResponse(BaseModel):
    """Response containing locally available Ollama models."""

    models: list[OllamaModelInfo]


def _format_size(size_bytes: int) -> str:
    """Convert byte count to a human-readable string (e.g. '2.0 GB')."""
    if size_bytes >= 1_000_000_000:
        return f"{size_bytes / 1_000_000_000:.1f} GB"
    if size_bytes >= 1_000_000:
        return f"{size_bytes / 1_000_000:.0f} MB"
    return f"{size_bytes / 1_000:.0f} KB"


@router.get(
    "/ollama-models",
    response_model=OllamaModelsResponse,
    status_code=status.HTTP_200_OK,
)
async def list_ollama_models_endpoint(
    base_url: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _user: User | None = Depends(require_admin_or_bootstrap),
) -> OllamaModelsResponse:
    """List locally available Ollama models.

    Queries the Ollama ``/api/tags`` endpoint and returns model names with
    human-readable sizes so the frontend can offer a selection dropdown.
    Returns an empty list when Ollama is unreachable.
    """
    settings = await load_settings(db)
    configured = base_url or settings.ollama_base_url
    if not configured:
        return OllamaModelsResponse(models=[])
    url = effective_ollama_base_url(configured)
    _validate_ollama_url(url)

    try:
        async with ollama_async_http_client(5.0) as client:
            response = await client.get(f"{url}/api/tags")
        response.raise_for_status()
    except httpx.HTTPError:
        return OllamaModelsResponse(models=[])

    result: list[OllamaModelInfo] = []
    for m in response.json().get("models", []):
        details = m.get("details", {})
        result.append(
            OllamaModelInfo(
                name=m.get("name", ""),
                size=_format_size(m.get("size", 0)),
                parameter_size=details.get("parameter_size"),
                quantization=details.get("quantization_level"),
            )
        )

    return OllamaModelsResponse(models=result)


class NerModelSuggestion(BaseModel):
    """Single preset model entry surfaced in the NER Models tab."""

    model_id: str
    label: str
    description: str


class NerDefaultsResponse(BaseModel):
    """Per-language defaults + curated alternative-model suggestions.

    ``defaults`` is keyed by ISO 639-1 language code; values are the
    ordered list of model IDs that make up that language's ensemble
    (one entry for single-model languages, two or more for ensembles).
    """

    defaults: dict[str, list[str]]
    suggestions: dict[str, list[NerModelSuggestion]]


@router.get(
    "/ner-defaults",
    response_model=NerDefaultsResponse,
    status_code=status.HTTP_200_OK,
)
async def get_ner_defaults(
    _user: User | None = Depends(require_admin_or_bootstrap),
) -> NerDefaultsResponse:
    """Return the backend's authoritative NER default-model map.

    The frontend NER Models settings tab used to hardcode its own copy of
    this dict; that copy drifted out of sync with the backend the first
    time the default models were upgraded (2026-03-12 XLM-RoBERTa
    upgrade), so the tab advertised stale model IDs. Sourcing the map from
    the live ``NERModelRegistry.DEFAULT_MODEL_MAP`` here eliminates the
    duplication — backend is the single source of truth.
    """
    from ..services.ner_model_registry import NERModelRegistry

    registry = NERModelRegistry()
    suggestions = {
        lang: [NerModelSuggestion(**entry) for entry in entries]
        for lang, entries in registry.SUGGESTED_MODELS.items()
    }
    return NerDefaultsResponse(
        defaults=dict(registry.DEFAULT_MODEL_MAP),
        suggestions=suggestions,
    )


class OllamaPullRequest(BaseModel):
    """Request body for pulling an Ollama model."""

    model: str
    base_url: Optional[str] = None


@router.post("/ollama-pull")
async def ollama_pull_endpoint(
    payload: OllamaPullRequest,
    db: AsyncSession = Depends(get_db),
    _user: User | None = Depends(require_admin_or_bootstrap),
) -> StreamingResponse:
    """Pull an Ollama model and stream download progress via SSE."""
    settings = await load_settings(db)
    configured = payload.base_url or settings.ollama_base_url
    if not configured:
        raise HTTPException(status_code=400, detail="Ollama base URL not configured.")
    base_url = effective_ollama_base_url(configured)
    _validate_ollama_url(base_url)

    import json as _json

    def _event(status_text: str, percent: int, done: bool, error: bool = False) -> str:
        evt: dict[str, Any] = {"status": status_text, "percent": percent, "done": done}
        if error:
            evt["error"] = True
        return f"data: {_json.dumps(evt)}\n\n"

    failed = _event("Model pull failed.", 0, True, error=True)

    async def event_stream():
        # Upstream status text and exception messages are never echoed to
        # the client: only fixed status strings and the progress percent.
        async with ollama_async_http_client(
            httpx.Timeout(connect=10.0, read=600.0, write=10.0, pool=10.0)
        ) as client:
            try:
                async with client.stream(
                    "POST",
                    f"{base_url}/api/pull",
                    json={"name": payload.model, "stream": True},
                ) as resp:
                    if resp.status_code >= 400:
                        logger.warning("Ollama pull rejected: http_status=%s", resp.status_code)
                        yield failed
                        return
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            data = _json.loads(line)
                        except _json.JSONDecodeError:
                            continue
                        if not isinstance(data, dict):
                            continue
                        if data.get("error"):
                            logger.warning("Ollama pull reported an error")
                            yield failed
                            return
                        if data.get("status") == "success":
                            yield _event("success", 100, True)
                            return
                        total = data.get("total") or 0
                        completed = data.get("completed") or 0
                        pct = int(completed * 100 / total) if total else 0
                        yield _event("downloading", pct, False)
            except Exception as exc:
                logger.warning("Ollama pull failed: %s", type(exc).__name__)
                yield failed

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get(
    "/ingestion/health",
    response_model=AudioPipelineHealthResponse,
    status_code=status.HTTP_200_OK,
)
async def get_ingestion_health_endpoint(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role("admin")),
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
        except Exception:
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
    _user: User = Depends(require_role("admin")),
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
    await require_registry_model_http(model_name)

    def _load_model_sync() -> None:
        ensure_whisper_model(model_name, wait=False)
        whisper.load_model(model_name, device=get_device())

    try:
        await asyncio.to_thread(_load_model_sync)
    except WhisperInstallBusy as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Model install already in progress",
        ) from exc
    except Exception as exc:
        logger.warning("Whisper model install failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to download or load Whisper model '{model_name}' ({public_error_text(exc)})",
        ) from exc

    return WhisperInstallResponse(
        status="ok",
        message=f"Whisper model '{model_name}' is available for ingestion.",
    )

