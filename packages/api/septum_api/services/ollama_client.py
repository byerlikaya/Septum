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
import threading
import time
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx

from .llm_errors import OLLAMA_URL_NOT_ALLOWED_CODE, LLMRouterError

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


def effective_ollama_base_url(base_url: str | None) -> str:
    """Return the base URL Ollama calls actually use (stored value, else default)."""
    return (base_url or _default_base_url()).rstrip("/")


OLLAMA_URL_REFUSED_MESSAGE = (
    "The configured Ollama URL is not allowed. Update the Ollama base URL in settings."
)


def ollama_url_refused(base_url: str | None) -> bool:
    """Return True when the effective Ollama base URL is refused by :func:`ollama_url_problem`."""
    return ollama_url_problem(effective_ollama_base_url(base_url)) is not None


def require_allowed_ollama_url(base_url: str | None) -> str:
    """Return the effective Ollama base URL, or raise the coded, host-free refusal."""
    base = effective_ollama_base_url(base_url)
    if ollama_url_problem(base) is not None:
        raise LLMRouterError(OLLAMA_URL_REFUSED_MESSAGE, code=OLLAMA_URL_NOT_ALLOWED_CODE)
    return base


def ollama_http_client(timeout: float | httpx.Timeout) -> httpx.Client:
    """Sync HTTP client for Ollama; ignores proxy env vars so prompts never reach a proxy."""
    return httpx.Client(timeout=timeout, trust_env=False)


def ollama_async_http_client(timeout: float | httpx.Timeout) -> httpx.AsyncClient:
    """Async HTTP client for Ollama; ignores proxy env vars so prompts never reach a proxy."""
    return httpx.AsyncClient(timeout=timeout, trust_env=False)


def _checked_url(base_url: str | None, endpoint: str) -> Optional[str]:
    """Return the URL of ``endpoint`` on the Ollama server, or ``None`` if the base URL is refused.

    Applied on every call so a base URL stored before the settings
    validator existed (or set through the environment) cannot reach a
    public or metadata host.
    """
    base = effective_ollama_base_url(base_url)
    if ollama_url_problem(base) is not None:
        logger.warning("Refusing Ollama call: configured base URL is not a local host")
        return None
    return f"{base}{endpoint}"


def _checked_generate_url(base_url: str | None) -> Optional[str]:
    """Return the ``/api/generate`` URL, or ``None`` if the base URL is refused."""
    return _checked_url(base_url, "/api/generate")


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


OLLAMA_NUM_PREDICT = 2048
"""Most tokens a sync call may generate."""

OLLAMA_NUM_CTX = 8192
"""Largest context window a detection call asks for, so it does not depend on the server default.

Ollama cuts a prompt longer than its window without saying so and still
reports ``done_reason == "stop"``. 8192 is twice the usual server default:
after :data:`OLLAMA_NUM_PREDICT` it leaves about 6000 tokens of prompt, which
holds the contextual prompt with its longest slice in any script (the
contextual layer sizes its slices in UTF-8 bytes to fit), while the key-value cache of a
3B model stays under 1 GB. A model trained on a shorter context gets that
length instead (see :func:`ollama_context_window`). A chat call to another
model asks for that model's whole trained context
(``call_ollama_async(full_context=True)``).
"""


class OllamaCallError(RuntimeError):
    """An Ollama call produced no reply: refused URL, transport/HTTP error or a malformed body.

    The message is a fixed failure kind and never carries the prompt, the
    URL or the server's text.
    """


_SHOW_TIMEOUT = 10.0

_WINDOW_TTL_SECONDS = 300.0
"""How long a trained context length read from ``/api/show`` is reused, so a re-tagged model is re-read."""

_FAILED_LOOKUP_TTL_SECONDS = 30.0
"""How long a lenient call skips ``/api/show`` after a failed read, so a hung server is not waited on every call."""

_context_windows: dict[tuple[str, str], tuple[float, Optional[int]]] = {}
"""Per server and model: the monotonic expiry time and the trained length, ``None`` for a failed read."""
_context_windows_lock = threading.Lock()


def _trained_context_length(data: object) -> Optional[int]:
    """The ``<architecture>.context_length`` of an ``/api/show`` body, or ``None`` when it is absent."""
    info = data.get("model_info") if isinstance(data, dict) else None
    if not isinstance(info, dict):
        return None
    architecture = info.get("general.architecture")
    if not isinstance(architecture, str) or not architecture:
        return None
    length = info.get(f"{architecture}.context_length")
    return length if type(length) is int and length > 0 else None


def _metadata_failure(exc: Exception) -> OllamaCallError:
    """Log the failure kind of a ``/api/show`` call and return the content-free error to raise."""
    logger.debug("Ollama model metadata call failed: %s", type(exc).__name__)
    return OllamaCallError("unknown_context_length")


def _show_url(base_url: str) -> str:
    """The checked ``/api/show`` URL of ``base_url``; raises ``OllamaCallError("url_refused")``."""
    url = _checked_url(base_url, "/api/show")
    if url is None:
        raise OllamaCallError("url_refused")
    return url


def _context_length_of(response: httpx.Response) -> int:
    """The trained context length an ``/api/show`` response reports.

    Raises ``OllamaCallError("unknown_context_length")`` for an error status,
    a body that is not JSON, or one without the length.
    """
    try:
        response.raise_for_status()
        length = _trained_context_length(response.json())
    except (httpx.HTTPError, ValueError) as exc:
        raise _metadata_failure(exc) from exc
    if length is None:
        raise OllamaCallError("unknown_context_length")
    return length


def _fetch_context_length(base_url: str, model: str) -> int:
    """Read the context length ``model`` was trained on from the server's ``/api/show``.

    Raises :class:`OllamaCallError` (``url_refused`` or
    ``unknown_context_length``) when the length cannot be read.
    """
    url = _show_url(base_url)
    try:
        with ollama_http_client(_SHOW_TIMEOUT) as client:
            return _context_length_of(client.post(url, json={"model": model}))
    except (httpx.HTTPError, ValueError) as exc:
        raise _metadata_failure(exc) from exc


async def _fetch_context_length_async(base_url: str, model: str) -> int:
    """Async twin of :func:`_fetch_context_length`."""
    url = _show_url(base_url)
    try:
        async with ollama_async_http_client(_SHOW_TIMEOUT) as client:
            return _context_length_of(await client.post(url, json={"model": model}))
    except (httpx.HTTPError, ValueError) as exc:
        raise _metadata_failure(exc) from exc


def _window_key(base_url: str | None, model: str | None) -> tuple[str, str]:
    """Cache key of a context window: the effective base URL and model id."""
    return effective_ollama_base_url(base_url), model or _default_deanon_model()


def _live_entry(key: tuple[str, str]) -> Optional[tuple[float, Optional[int]]]:
    """The cache entry of ``key`` unless it has expired."""
    with _context_windows_lock:
        entry = _context_windows.get(key)
    return entry if entry is not None and time.monotonic() < entry[0] else None


def _store_length(key: tuple[str, str], trained: Optional[int]) -> None:
    """Cache a trained length, or a failed read (``None``) for the shorter failure TTL."""
    ttl = _WINDOW_TTL_SECONDS if trained is not None else _FAILED_LOOKUP_TTL_SECONDS
    with _context_windows_lock:
        _context_windows[key] = (time.monotonic() + ttl, trained)


def _read_trained_length(key: tuple[str, str]) -> int:
    """Read and cache the trained length of ``key``; a failed read is cached as ``None`` and raises."""
    try:
        trained = _fetch_context_length(*key)
    except OllamaCallError:
        _store_length(key, None)
        raise
    _store_length(key, trained)
    return trained


def _trained_length(base_url: str | None, model: str | None) -> Optional[int]:
    """The trained context length of ``model``, or ``None`` when it cannot be read.

    For lenient callers: a failed read is remembered for
    :data:`_FAILED_LOOKUP_TTL_SECONDS`, so a hung server is not waited on
    again by every call.
    """
    key = _window_key(base_url, model)
    entry = _live_entry(key)
    if entry is not None:
        return entry[1]
    try:
        return _read_trained_length(key)
    except OllamaCallError:
        return None


async def _trained_length_async(base_url: str | None, model: str | None) -> Optional[int]:
    """Async twin of :func:`_trained_length`, sharing its cache."""
    key = _window_key(base_url, model)
    entry = _live_entry(key)
    if entry is not None:
        return entry[1]
    try:
        trained = await _fetch_context_length_async(*key)
    except OllamaCallError:
        trained = None
    _store_length(key, trained)
    return trained


def _capped(trained: Optional[int]) -> Optional[int]:
    """``min(OLLAMA_NUM_CTX, trained)``, or ``None`` for an unknown length."""
    return None if trained is None else min(OLLAMA_NUM_CTX, trained)


def ollama_context_window(base_url: str | None = None, model: str | None = None) -> int:
    """The window a detection call to ``model`` really gets: ``min(OLLAMA_NUM_CTX, trained context length)``.

    Ollama lowers a requested ``num_ctx`` to the length the model was
    trained on and then cuts the prompt to fit, so every length check must
    use the smaller of the two. The trained length is read per server and
    model and cached for :data:`_WINDOW_TTL_SECONDS`; a failed read is
    never taken from the cache here and raises :class:`OllamaCallError`
    (``url_refused`` or ``unknown_context_length``), so a caller that must
    not fail open refuses instead of guessing.
    """
    key = _window_key(base_url, model)
    entry = _live_entry(key)
    trained = entry[1] if entry is not None else None
    if trained is None:
        trained = _read_trained_length(key)
    return min(OLLAMA_NUM_CTX, trained)


def uses_detection_model(
    base_url: str | None,
    model: str | None,
    detection_base_url: str | None,
    detection_model: str | None,
) -> bool:
    """Whether a call to ``model`` on ``base_url`` reaches the same server and model as the detection calls."""
    return _window_key(base_url, model) == _window_key(detection_base_url, detection_model)


def ollama_request_options(
    window: Optional[int], options: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The ``options`` of an Ollama request: the caller's, with ``num_ctx`` set to ``window``.

    Every detection request to one model carries the same ``num_ctx``, so
    the server never reloads the model because two callers asked for
    different windows. With ``window=None`` (length unknown) ``num_ctx`` is
    left out and the server default applies.
    """
    merged = dict(options or {})
    if window is not None:
        merged["num_ctx"] = window
    return merged


def request_ollama_sync(
    prompt: str,
    base_url: str | None = None,
    model: str | None = None,
    timeout: float = 30.0,
    allow_truncated: bool = True,
) -> str:
    """Synchronously call Ollama /api/generate and return the response text.

    Raises :class:`OllamaCallError` when no reply was obtained, so a caller
    that must not fail open can tell a failed call from an empty answer.
    With ``allow_truncated=False`` a reply that may not cover the whole
    prompt raises too: one cut at the token limit (``done_reason ==
    "length"``), one whose measured prompt (``prompt_eval_count``) plus
    :data:`OLLAMA_NUM_PREDICT` reaches the model's real window
    (:func:`ollama_context_window`) or is not reported, and any call to a
    model whose window cannot be read, since the server may have cut the
    prompt to fit its window. Otherwise an unreadable window leaves
    ``num_ctx`` out, so the server default applies.
    """
    url = _checked_generate_url(base_url)
    if url is None:
        raise OllamaCallError("url_refused")
    strict_window: Optional[int] = None
    if allow_truncated:
        window = _capped(_trained_length(base_url, model))
    else:
        strict_window = window = ollama_context_window(base_url, model)
    payload: dict[str, Any] = {
        "model": model or _default_deanon_model(),
        "prompt": prompt,
        "stream": False,
        "options": ollama_request_options(window, {"num_predict": OLLAMA_NUM_PREDICT}),
    }
    try:
        with ollama_http_client(timeout) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        logger.debug("Ollama sync call failed: %s", type(exc).__name__)
        raise OllamaCallError(type(exc).__name__) from exc
    except ValueError as exc:
        raise OllamaCallError("invalid_body") from exc
    out = data.get("response") if isinstance(data, dict) else None
    if not isinstance(out, str):
        raise OllamaCallError("invalid_body")
    if strict_window is not None:
        if data.get("done_reason") == "length":
            raise OllamaCallError("truncated_reply")
        _require_whole_prompt(data.get("prompt_eval_count"), strict_window)
    return out


def _require_whole_prompt(prompt_eval_count: object, window: int) -> None:
    """Raise unless the server measured a prompt that fits ``window`` with room to answer."""
    if type(prompt_eval_count) is not int:
        raise OllamaCallError("unmeasured_prompt")
    if prompt_eval_count + OLLAMA_NUM_PREDICT >= window:
        logger.warning(
            "Ollama prompt of %d tokens leaves no room in a %d-token window",
            prompt_eval_count,
            window,
        )
        raise OllamaCallError("context_overflow")


def call_ollama_sync(
    prompt: str,
    base_url: str | None = None,
    model: str | None = None,
    timeout: float = 30.0,
) -> str:
    """Synchronously call Ollama /api/generate; return ``""`` when no reply was obtained.

    For callers whose fallback on an empty reply is safe; see
    :func:`request_ollama_sync` for the raising variant.
    """
    try:
        return request_ollama_sync(prompt, base_url=base_url, model=model, timeout=timeout)
    except OllamaCallError:
        return ""


async def call_ollama_async(
    prompt: str,
    base_url: str | None = None,
    model: str | None = None,
    timeout: float = 30.0,
    options: dict[str, Any] | None = None,
    full_context: bool = False,
) -> str:
    """Asynchronously call Ollama /api/generate and return the response text.

    Returns ``""`` (the same contract as an unreachable server) when the
    base URL is refused by :func:`ollama_url_problem`. The request carries
    ``num_ctx``: the model's whole trained context with ``full_context``,
    so a long prompt is not cut, otherwise the same capped window as a sync
    call to the same model (:func:`ollama_request_options`). An unreadable
    length leaves ``num_ctx`` out, so the server default applies.
    """
    url = _checked_generate_url(base_url)
    if url is None:
        return ""
    trained = await _trained_length_async(base_url, model)
    window = trained if full_context else _capped(trained)
    payload: dict[str, Any] = {
        "model": model or _default_deanon_model(),
        "prompt": prompt,
        "stream": False,
        "options": ollama_request_options(window, options),
    }
    try:
        async with ollama_async_http_client(timeout) as client:
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


_EMPTY_REPLY_RE = re.compile(r"(?:```(?:json)?\s*)?\[\s*\]\s*(?:```)?", re.IGNORECASE)
"""A whole reply that is an empty JSON array, optionally fenced: the model found nothing."""


def reply_items(response: str) -> Optional[list[dict[str, Any]]]:
    """Return the span items of a detection reply, or ``None`` when it cannot be trusted.

    Only a reply that is exactly an empty array (optionally fenced) means
    "found nothing". String items are taken as span texts. A reply with no
    item that is a non-empty string or a dict with a non-empty ``text`` is
    unparsable, so a malformed answer never passes as an empty one.
    """
    if _EMPTY_REPLY_RE.fullmatch(response.strip()):
        return []
    items: list[dict[str, Any]] = []
    for item in extract_json_array(response):
        if isinstance(item, str):
            item = {"text": item}
        if isinstance(item, dict):
            items.append(item)
    if not any(item.get("text") not in (None, "") for item in items):
        return None
    return items
