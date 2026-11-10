"""Whether special-category masking can be on by default.

The contextual layer detects special-category data (diagnosis, religion,
biometric data and so on) through the local model, and it refuses a request
when that model fails. It is therefore switched on at setup completion only
when a local model server is configured, reachable and has the model the layer
uses installed.
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging

import httpx
from septum_core.detector import _CONTEXTUAL_ENTITY_TYPES
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..env_flags import env_bool
from ..models.regulation import RegulationRuleset
from ..models.settings import AppSettings
from .ollama_client import (
    effective_ollama_base_url,
    ollama_async_http_client,
    ollama_model_installed,
    ollama_url_problem,
)

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 3.0
MAX_PROBE_BODY_BYTES = 1024 * 1024
OPT_OUT_ENV = "USE_OLLAMA_SEMANTIC_LAYER_DEFAULT"


class DefaultReason(enum.StrEnum):
    """Why the contextual layer was or was not switched on at setup completion."""

    ENABLED = "enabled"
    NO_LOCAL_MODEL = "no_local_model"
    ENV_OPT_OUT = "env_opt_out"
    NO_CONTEXTUAL_TYPES = "no_contextual_types"
    PROBE_URL_NOT_ALLOWED = "probe_url_not_allowed"
    PROBE_UNREACHABLE = "probe_unreachable"
    PROBE_TIMEOUT = "probe_timeout"
    PROBE_HTTP_ERROR = "probe_http_error"
    PROBE_INVALID_RESPONSE = "probe_invalid_response"
    PROBE_MODEL_MISSING = "probe_model_missing"


async def active_contextual_types(db: AsyncSession) -> frozenset[str]:
    """Return the contextual entity types listed by the active regulation rulesets."""
    rows = await db.execute(
        select(RegulationRuleset.entity_types).where(RegulationRuleset.is_active.is_(True))
    )
    listed: set[str] = set()
    for (entity_types,) in rows:
        listed.update(entity_types or [])
    return frozenset(listed & _CONTEXTUAL_ENTITY_TYPES)


async def _read_bounded(response: httpx.Response) -> bytes | None:
    """Read the body, or return None when it exceeds :data:`MAX_PROBE_BODY_BYTES`."""
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_PROBE_BODY_BYTES:
        return None
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > MAX_PROBE_BODY_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


async def probe_local_pii_model(settings: AppSettings) -> DefaultReason:
    """Return ``ENABLED`` when the local server has the contextual model, else the probe failure.

    The URL goes through the same SSRF guard as every other Ollama call.
    """
    base_url = effective_ollama_base_url(settings.ollama_base_url)
    if ollama_url_problem(base_url) is not None:
        return DefaultReason.PROBE_URL_NOT_ALLOWED
    try:
        async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
            async with ollama_async_http_client(PROBE_TIMEOUT_SECONDS) as client:
                async with client.stream("GET", f"{base_url}/api/tags") as response:
                    if not response.is_success:
                        return DefaultReason.PROBE_HTTP_ERROR
                    body = await _read_bounded(response)
    except (TimeoutError, httpx.TimeoutException):
        return DefaultReason.PROBE_TIMEOUT
    except httpx.HTTPError:
        return DefaultReason.PROBE_UNREACHABLE
    if body is None:
        return DefaultReason.PROBE_INVALID_RESPONSE
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError):
        return DefaultReason.PROBE_INVALID_RESPONSE
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        return DefaultReason.PROBE_INVALID_RESPONSE
    names = [
        entry["name"]
        for entry in payload["models"]
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    ]
    if not ollama_model_installed(settings.ollama_deanon_model or "", names):
        return DefaultReason.PROBE_MODEL_MISSING
    return DefaultReason.ENABLED


def _wizard_chose_local_model(settings: AppSettings) -> bool:
    """Return True when the wizard selected the local provider or the local detection layer."""
    return settings.llm_provider == "ollama" or bool(settings.use_ollama_layer)


def _env_opt_out() -> bool:
    """Return True when the operator explicitly set the default flag to false."""
    return not env_bool(OPT_OUT_ENV, True)


async def decide_contextual_default(db: AsyncSession, settings: AppSettings) -> DefaultReason:
    """Decide ``use_ollama_semantic_layer`` for a setup being completed; ``ENABLED`` means on.

    The probe runs last and only when it can matter.
    """
    if not _wizard_chose_local_model(settings):
        return DefaultReason.NO_LOCAL_MODEL
    if _env_opt_out():
        return DefaultReason.ENV_OPT_OUT
    with db.no_autoflush:
        has_types = bool(await active_contextual_types(db))
    if not has_types:
        return DefaultReason.NO_CONTEXTUAL_TYPES
    return await probe_local_pii_model(settings)


async def apply_setup_completion_default(
    db: AsyncSession, settings: AppSettings, was_completed: bool, update_data: dict
) -> None:
    """Set ``use_ollama_semantic_layer`` when this update completes setup and does not choose it."""
    if (
        was_completed
        or not settings.setup_completed
        or "use_ollama_semantic_layer" in update_data
        or settings.use_ollama_semantic_layer
    ):
        return
    reason = await decide_contextual_default(db, settings)
    settings.use_ollama_semantic_layer = reason is DefaultReason.ENABLED
    logger.info("Contextual layer default at setup completion: %s", reason.value)
