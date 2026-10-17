from __future__ import annotations

"""
Shared Ollama HTTP client for Septum.

All Ollama usage (sanitizer Layer 3, deanonymizer ollama strategy,
LLMContextRecognizer) is always enabled and routed through this client.
Connection uses OLLAMA_BASE_URL.
"""

import ipaddress
import json
import logging
import os
import re
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

_ALLOWED_OLLAMA_HOSTS = frozenset(
    {"localhost", "host.docker.internal", "host.containers.internal", "ollama"}
)
_ALLOWED_OLLAMA_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
)
_ALLOWED_OLLAMA_SCHEMES = frozenset({"http", "https"})


def _is_allowed_ollama_host(hostname: str) -> bool:
    """Return True for a known local name or a canonical loopback IP literal.

    Only canonical address forms pass: ``ipaddress`` rejects decimal
    (``2130706433``), hex (``0x7f.1``), short (``127.1``) and leading-zero
    octal IPv4 spellings, and any other DNS name is refused because it
    could resolve to a remote or metadata address after validation.
    """
    if hostname in _ALLOWED_OLLAMA_HOSTS:
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return any(address in network for network in _ALLOWED_OLLAMA_NETWORKS)


def ollama_url_problem(url: str) -> Optional[str]:
    """Return why ``url`` is not an acceptable Ollama base URL, or ``None``.

    Prevents SSRF: the scheme must be http(s), the URL must carry no
    userinfo, and the host must be a loopback address literal or one of
    the local container-host names; private-network (RFC 1918 / ULA)
    addresses are refused. Trailing-dot and scoped hostnames are refused.
    The URL names the server only: a path other than ``/``, a query,
    a fragment or ``;`` parameters are refused, so a base URL cannot point
    the client at another service's API on an allowed host.
    """
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        netloc = parts.netloc
        hostname = parts.hostname or ""
        _ = parts.port  # raises ValueError on a malformed port
    except ValueError:
        return "Ollama URL is malformed."
    if scheme not in _ALLOWED_OLLAMA_SCHEMES:
        return "Ollama URL must use the http or https scheme."
    if "@" in netloc or "\\" in netloc:
        return "Ollama URL must not contain credentials."
    if parts.path not in ("", "/") or any(ch in url for ch in "?#;"):
        return "Ollama URL must not contain a path, query, fragment or parameters."
    if (
        not hostname
        or hostname.endswith(".")
        or "%" in hostname
        or not _is_allowed_ollama_host(hostname.lower())
    ):
        return (
            f"Ollama URL host '{hostname}' is not allowed. Use localhost, "
            "a loopback IP address, host.docker.internal, "
            "host.containers.internal, or ollama."
        )
    return None


def _checked_generate_url(base_url: str | None) -> Optional[str]:
    """Return the ``/api/generate`` URL, or ``None`` if the base URL is refused.

    Applied on every call so a base URL stored before the settings
    validator existed (or set through the environment) cannot reach a
    public or metadata host.
    """
    base = (base_url or _default_base_url()).rstrip("/")
    if ollama_url_problem(base) is not None:
        logger.warning("Refusing Ollama call: configured base URL is not a local host")
        return None
    return f"{base}/api/generate"


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
    """Synchronously call Ollama /api/generate and return the response text.

    Returns ``""`` (the same contract as an unreachable server) when the
    base URL is refused by :func:`ollama_url_problem`.
    """
    url = _checked_generate_url(base_url)
    if url is None:
        return ""
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
        logger.debug("Ollama sync call failed: %s", type(exc).__name__)
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
    """Asynchronously call Ollama /api/generate and return the response text.

    Returns ``""`` (the same contract as an unreachable server) when the
    base URL is refused by :func:`ollama_url_problem`.
    """
    url = _checked_generate_url(base_url)
    if url is None:
        return ""
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
        logger.debug("Ollama async call failed: %s", type(exc).__name__)
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
        "Could not extract valid JSON array from Ollama response (length=%d)",
        len(stripped),
    )
    return []
