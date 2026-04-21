from __future__ import annotations

"""
Shared Ollama HTTP client for Septum.

All Ollama usage (sanitizer Layer 3, deanonymizer ollama strategy,
LLMContextRecognizer) is always enabled and routed through this client.
Connection uses OLLAMA_BASE_URL.
"""

import json
import logging
import os
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def use_ollama_enabled() -> bool:
    """Return True when Ollama integration is considered enabled.

    This helper always returns True and exists for backward compatibility with
    earlier configurations that gated Ollama usage behind an environment
    variable. Runtime behavior still gracefully degrades to non-Ollama
    fallbacks if the local service is unreachable.
    """
    return True


def _default_base_url() -> str:
    from ..config import default_ollama_url
    return os.getenv("OLLAMA_BASE_URL", default_ollama_url()).rstrip("/")


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
        "options": {
            "num_predict": 2048,
        },
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
    options: dict[str, Any] | None = None,
) -> str:
    """Asynchronously call Ollama /api/generate and return the response text."""
    url = f"{(base_url or _default_base_url())}/api/generate"
    payload: dict[str, Any] = {
        "model": model or _default_deanon_model(),
        "prompt": prompt,
        "stream": False,
    }
    if options:
        payload["options"] = options
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
    """Extract a JSON array from LLM output, tolerating markdown or extra text.

    This parser handles multiple common LLM output formats:
    - Bare JSON array: [...]
    - Markdown code fence: ```json [...] ```
    - Multiple JSON arrays (returns first valid one)
    - Text before/after JSON
    - Malformed trailing commas or extra whitespace
    """
    if not text or not text.strip():
        return []

    stripped = text.strip()

    # Strategy 1: Try removing markdown code fences first
    cleaned = re.sub(r"```(?:json)?\s*", "", stripped)
    cleaned = re.sub(r"```\s*$", "", cleaned).strip()

    # Strategy 1b: Try parsing the fence-cleaned text directly. Handles the
    # common case where the model wraps a clean JSON array in a ```json fence.
    # Running this before the regex strategy avoids false matches on `]`
    # characters that legitimately appear inside placeholder strings like
    # "[PERSON_NAME_1]".
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    # Strategy 2: Find all potential JSON arrays (greedy match from [ to ])
    # Use a more conservative pattern that matches balanced brackets
    potential_arrays = re.finditer(r"\[[\s\S]*?\](?=\s*(?:\]|$|```|[^,\s\[\]]))", cleaned)

    for match in potential_arrays:
        candidate = match.group(0)
        candidate = candidate.strip()
        # Remove trailing commas before closing brackets (common LLM mistake)
        candidate = re.sub(r",(\s*\])", r"\1", candidate)

        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, list):
                logger.debug("Successfully parsed JSON array with %d items", len(parsed))
                return parsed
        except json.JSONDecodeError:
            continue

    # Strategy 3: Last resort - try the entire stripped text
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    logger.warning(
        "Could not extract valid JSON array from Ollama response (length=%d). "
        "First 200 chars: %s",
        len(stripped),
        stripped[:200],
    )
    return []
