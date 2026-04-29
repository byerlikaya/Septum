"""Provider-specific HTTP clients for cloud LLM APIs.

Each forwarder takes a :class:`RequestEnvelope` and returns the masked
answer text as a string. Failure surfaces as :class:`GatewayError` so
the caller (``GatewayConsumer``) can package it into a
:class:`ResponseEnvelope` with ``error`` set.

Why copy the provider classes from ``septum-api`` instead of sharing?
The two zones must remain code-isolated. The air-gap zone keeps its
own LLM provider package so an on-prem Ollama-only deployment never
depends on ``septum-gateway`` and therefore never drags ``fastapi`` or
the cloud provider URLs into its trust boundary. The two copies are
thin and change infrequently; duplication is cheaper than the
shared-library blast radius.

Hardening invariants:

* ``base_url`` (envelope-supplied) is validated against a per-provider
  allow-list before dialing. Internal addresses (RFC1918, loopback,
  link-local cloud metadata, etc.) are rejected — a compromised queue
  must not be able to redirect the gateway's outbound calls.
* Cloud-provider API keys come exclusively from the gateway's own
  ``GatewayConfig`` (env-loaded). Envelopes no longer carry secrets.
* One ``httpx.AsyncClient`` per forwarder is reused across requests
  (TCP/TLS handshake reuse) instead of recreated per attempt.
* ``Idempotency-Key: <correlation_id>`` is sent so an upstream retry
  cannot double-bill the user when the network blip happens after the
  request reached the provider.
* Retries are limited to 429 / 5xx / network errors and honor
  ``Retry-After`` when present. 4xx errors fail fast.
* Backoff uses full-jitter (``random.uniform(0, base * 2**n)``) so
  multiple gateway replicas hitting the same provider 429 do not
  thunder-herd in lockstep.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import random
import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, List, Mapping, Protocol
from urllib.parse import urlparse

import httpx
from septum_queue import RequestEnvelope

if TYPE_CHECKING:
    from .config import GatewayConfig

logger = logging.getLogger(__name__)


class GatewayError(RuntimeError):
    """Raised when a gateway forwarder call fails."""


class ForwarderLike(Protocol):
    """Duck-typed contract the consumer expects from a forwarder."""

    async def complete(self, envelope: RequestEnvelope) -> str: ...


_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}

# Per-provider host allow-list. ``base_url`` from the envelope must
# resolve to one of these hostnames OR be empty (use the default).
_PROVIDER_ALLOWED_HOSTS: dict[str, frozenset[str]] = {
    "anthropic": frozenset({"api.anthropic.com"}),
    "openai": frozenset({"api.openai.com"}),
    "openrouter": frozenset({"openrouter.ai"}),
}

# Strings that look like API keys / bearer tokens — stripped from any
# error envelope text before it crosses the bridge back to the
# air-gapped side. Defense-in-depth against a future provider error
# format that echoes auth headers.
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{16,}", re.IGNORECASE),
    re.compile(r"x-api-key:\s*\S+", re.IGNORECASE),
]


def _sanitize_error_text(text: str) -> str:
    out = text
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub("<redacted>", out)
    return out[:500]


def _validate_base_url(provider: str, base_url: str | None, default_url: str) -> str:
    """Return the URL to dial, rejecting any base_url not on the allow-list.

    Empty / missing ``base_url`` falls through to the provider default.
    Otherwise the host must match the per-provider allow-list AND must
    not resolve to a private/loopback/link-local address.
    """
    if not base_url:
        return default_url
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise GatewayError(
            f"disallowed base_url scheme {parsed.scheme!r}; expected http(s)"
        )
    host = (parsed.hostname or "").lower()
    if not host:
        raise GatewayError("base_url is missing a hostname")
    allowed = _PROVIDER_ALLOWED_HOSTS.get(provider.strip().lower(), frozenset())
    if host not in allowed:
        raise GatewayError(
            f"base_url host {host!r} is not on the allow-list for provider {provider!r}"
        )
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise GatewayError(f"base_url host {host!r} is a private address")
    except ValueError:
        # Not a literal IP; the allow-list already constrains the FQDN.
        pass
    if parsed.scheme != "https":
        raise GatewayError("base_url must use https://")
    return base_url


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


class BaseForwarder(ABC):
    """Shared behavior for every provider-specific forwarder."""

    def __init__(
        self,
        *,
        api_key: str | None,
        timeout_seconds: float = 30.0,
        max_attempts: int = 3,
        base_backoff_seconds: float = 0.5,
        max_concurrent: int = 4,
    ) -> None:
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._base_backoff_seconds = base_backoff_seconds
        # One client per forwarder so TCP+TLS handshakes amortize across
        # requests. ``Limits`` caps outbound concurrency per provider.
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds,
            limits=httpx.Limits(
                max_connections=max_concurrent * 2,
                max_keepalive_connections=max_concurrent,
            ),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _resolve_api_key(self) -> str:
        if not self._api_key:
            raise GatewayError(
                f"{self._provider_name} API key is not configured. "
                f"Set the matching SEPTUM_GATEWAY_*_API_KEY env var."
            )
        return self._api_key

    @property
    @abstractmethod
    def _provider_name(self) -> str: ...

    @abstractmethod
    async def complete(self, envelope: RequestEnvelope) -> str: ...

    async def _post_with_retries(
        self,
        url: str,
        headers: Mapping[str, str],
        json: Mapping[str, Any],
        *,
        correlation_id: str,
    ) -> httpx.Response:
        """POST with full-jitter backoff. Retries only on 5xx/429/transport."""
        attempt = 0
        last_error: Exception | None = None
        outgoing_headers = dict(headers)
        outgoing_headers.setdefault("Idempotency-Key", correlation_id)
        while attempt < self._max_attempts:
            attempt += 1
            try:
                response = await self._client.post(
                    url, headers=outgoing_headers, json=dict(json)
                )
                if response.status_code in _RETRYABLE_STATUS:
                    last_error = httpx.HTTPStatusError(
                        f"upstream {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                    logger.warning(
                        "gateway upstream retryable status: url=%s status=%s attempt=%s/%s",
                        url,
                        response.status_code,
                        attempt,
                        self._max_attempts,
                    )
                    if attempt >= self._max_attempts:
                        break
                    sleep_for = _retry_after_seconds(response)
                    if sleep_for is None:
                        sleep_for = random.uniform(
                            0.0, self._base_backoff_seconds * (2 ** (attempt - 1))
                        )
                    await asyncio.sleep(sleep_for)
                    continue
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                # Non-retryable 4xx — fail fast.
                logger.error(
                    "gateway upstream non-retryable status: url=%s status=%s",
                    url,
                    exc.response.status_code,
                )
                raise GatewayError(
                    f"upstream provider rejected request "
                    f"(status={exc.response.status_code})"
                ) from exc
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning(
                    "gateway transport error: url=%s error=%s attempt=%s/%s",
                    url,
                    type(exc).__name__,
                    attempt,
                    self._max_attempts,
                )
                if attempt >= self._max_attempts:
                    break
                await asyncio.sleep(
                    random.uniform(
                        0.0, self._base_backoff_seconds * (2 ** (attempt - 1))
                    )
                )

        raise GatewayError(
            f"upstream provider request failed after {self._max_attempts} attempts"
        ) from last_error


class AnthropicForwarder(BaseForwarder):
    """Anthropic Messages API forwarder."""

    _DEFAULT_URL = "https://api.anthropic.com/v1/messages"

    @property
    def _provider_name(self) -> str:
        return "Anthropic"

    async def complete(self, envelope: RequestEnvelope) -> str:
        api_key = self._resolve_api_key()
        url = _validate_base_url(envelope.provider, envelope.base_url, self._DEFAULT_URL)
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        body: dict[str, Any] = {
            "model": envelope.model,
            "temperature": float(envelope.temperature),
            "max_tokens": envelope.max_tokens or 1024,
            "messages": list(envelope.messages),
        }
        response = await self._post_with_retries(
            url=url,
            headers=headers,
            json=body,
            correlation_id=envelope.correlation_id,
        )
        data = response.json()
        blocks = data.get("content") or []
        parts: List[str] = []
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                value = block.get("text")
                if isinstance(value, str):
                    parts.append(value)
        return "".join(parts)


class _OpenAICompatibleForwarder(BaseForwarder):
    """Shared code path for OpenAI Chat Completions-shaped providers."""

    @property
    @abstractmethod
    def _default_url(self) -> str: ...

    @property
    def _extra_headers(self) -> Mapping[str, str]:
        return {}

    async def complete(self, envelope: RequestEnvelope) -> str:
        api_key = self._resolve_api_key()
        url = _validate_base_url(envelope.provider, envelope.base_url, self._default_url)
        headers: dict[str, str] = {"Authorization": f"Bearer {api_key}"}
        headers.update(self._extra_headers)
        body: dict[str, Any] = {
            "model": envelope.model,
            "temperature": float(envelope.temperature),
            "messages": list(envelope.messages),
        }
        if envelope.max_tokens is not None:
            body["max_tokens"] = envelope.max_tokens
        response = await self._post_with_retries(
            url=url,
            headers=headers,
            json=body,
            correlation_id=envelope.correlation_id,
        )
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise GatewayError(
                f"{self._provider_name} response did not contain any choices"
            )
        content = (choices[0].get("message") or {}).get("content")
        if not isinstance(content, str):
            raise GatewayError(
                f"{self._provider_name} response did not contain text content"
            )
        return content


class OpenAIForwarder(_OpenAICompatibleForwarder):
    """OpenAI Chat Completions forwarder."""

    @property
    def _provider_name(self) -> str:
        return "OpenAI"

    @property
    def _default_url(self) -> str:
        return "https://api.openai.com/v1/chat/completions"


class OpenRouterForwarder(_OpenAICompatibleForwarder):
    """OpenRouter Chat Completions forwarder."""

    @property
    def _provider_name(self) -> str:
        return "OpenRouter"

    @property
    def _default_url(self) -> str:
        return "https://openrouter.ai/api/v1/chat/completions"

    @property
    def _extra_headers(self) -> Mapping[str, str]:
        return {
            "HTTP-Referer": "https://septum.local",
            "X-Title": "Septum",
        }


class ForwarderRegistry:
    """Builds the correct forwarder for a given provider string.

    The registry is intentionally shallow: it is just a dict-backed
    factory. Tests can register a fake forwarder to short-circuit
    HTTP without monkey-patching httpx globally.
    """

    def __init__(
        self,
        *,
        anthropic: ForwarderLike | None = None,
        openai: ForwarderLike | None = None,
        openrouter: ForwarderLike | None = None,
    ) -> None:
        self._forwarders: dict[str, ForwarderLike] = {}
        if anthropic is not None:
            self._forwarders["anthropic"] = anthropic
        if openai is not None:
            self._forwarders["openai"] = openai
        if openrouter is not None:
            self._forwarders["openrouter"] = openrouter

    @classmethod
    def from_config(cls, config: "GatewayConfig") -> "ForwarderRegistry":
        """Build the default registry using a :class:`GatewayConfig`."""
        return cls(
            anthropic=AnthropicForwarder(
                api_key=config.anthropic_api_key,
                timeout_seconds=config.request_timeout_seconds,
                max_attempts=config.max_attempts,
            ),
            openai=OpenAIForwarder(
                api_key=config.openai_api_key,
                timeout_seconds=config.request_timeout_seconds,
                max_attempts=config.max_attempts,
            ),
            openrouter=OpenRouterForwarder(
                api_key=config.openrouter_api_key,
                timeout_seconds=config.request_timeout_seconds,
                max_attempts=config.max_attempts,
            ),
        )

    def for_provider(self, provider: str) -> ForwarderLike:
        key = provider.strip().lower()
        try:
            return self._forwarders[key]
        except KeyError as exc:
            raise GatewayError(f"unsupported provider: {provider!r}") from exc

    def register(self, provider: str, forwarder: ForwarderLike) -> None:
        """Used in tests to substitute a fake forwarder."""
        self._forwarders[provider.strip().lower()] = forwarder

    async def aclose(self) -> None:
        """Close every owned forwarder's underlying HTTP client."""
        for forwarder in self._forwarders.values():
            close = getattr(forwarder, "aclose", None)
            if close is not None:
                await close()
