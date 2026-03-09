from __future__ import annotations

"""
Shared Ollama HTTP client and USE_OLLAMA gate for Septum.

All Ollama usage (sanitizer Layer 3, deanonymizer ollama strategy,
LLMContextRecognizer) is gated by USE_OLLAMA. When USE_OLLAMA=false,
these layers are skipped. Connection uses OLLAMA_BASE_URL.
"""

import json
import logging
import os
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def use_ollama_enabled() -> bool:
    """Return True if Ollama integration is enabled (USE_OLLAMA env)."""
    value = os.getenv("USE_OLLAMA", "true")
    if value is None:
        return True
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _default_base_url() -> str:
    return os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")


def _default_deanon_model() -> str:
    return os.getenv("OLLAMA_DEANON_MODEL", "llama3.2:3b")


def call_ollama_sync(
    prompt: str,
    base_url: str | None = None,
    model: str | None = None,
    timeout: float = 30.0,
) -> str:
    """Synchronously call Ollama /api/generate and return the response text."""
    url = f"{(base_url or _default_base_url())}/api/generate"
    payload: dict[str, Any] = {
        "model": model or _default_deanon_model(),
        "prompt": prompt,
        "stream": False,
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        logger.debug("Ollama sync call failed: %s", exc)
        return ""
    out = data.get("response")
    return out if isinstance(out, str) else ""


async def call_ollama_async(
    prompt: str,
    base_url: str | None = None,
    model: str | None = None,
    timeout: float = 30.0,
) -> str:
    """Asynchronously call Ollama /api/generate and return the response text."""
    url = f"{(base_url or _default_base_url())}/api/generate"
    payload: dict[str, Any] = {
        "model": model or _default_deanon_model(),
        "prompt": prompt,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        logger.debug("Ollama async call failed: %s", exc)
        return ""
    out = data.get("response")
    return out if isinstance(out, str) else ""


def extract_json_array(text: str) -> list[dict[str, Any]]:
    """Extract a JSON array from LLM output, tolerating markdown or extra text."""
    if not text or not text.strip():
        return []
    stripped = text.strip()
    match = re.search(r"\[[\s\S]*\]", stripped)
    if not match:
        logger.warning("Ollama response contained no JSON array; raw (first 300 chars): %s", stripped[:300])
        return []
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError as e:
        logger.warning(
            "Ollama JSON parse failed: %s; snippet: %s",
            e,
            match.group(0)[:200],
        )
        return []
