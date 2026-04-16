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
thin (≤60 lines each) and change infrequently; duplication is cheaper
than the shared-library blast radius.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, List, Mapping, Protocol

import httpx
from septum_queue import RequestEnvelope

logger = logging.getLogger(__name__)


class GatewayError(RuntimeError):
    """Raised when a gateway forwarder call fails."""


class ForwarderLike(Protocol):
    """Duck-typed contract the consumer expects from a forwarder."""

    async def complete(self, envelope: RequestEnvelope) -> str: ...


async def _post_with_retries(
    url: str,
    headers: Mapping[str, str],
    json: Mapping[str, Any],
    *,
    timeout_seconds: float,
    max_attempts: int,
) -> httpx.Response:
    """POST with exponential backoff on transient errors.

    Mirrors the retry policy used by the air-gapped side's provider
    layer — keeps the user-visible latency / retry behavior identical
    regardless of whether the request went via gateway or direct call.
    """
    attempt = 0
    last_error: Exception | None = None
    base_backoff = 0.5
    while attempt < max_attempts:
        attempt += 1
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(
                    url, headers=dict(headers), json=dict(json)
                )
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            last_error = exc
            logger.error(
                "gateway upstream returned error status: url=%s status=%s",
                url,
                exc.response.status_code,
            )
            if attempt >= max_attempts:
                break
            await asyncio.sleep(base_backoff * (2 ** (attempt - 1)))
        except httpx.HTTPError as exc:
            last_error = exc
            logger.error("gateway transport error: url=%s error=%s", url, exc)
            if attempt >= max_attempts:
                break
            await asyncio.sleep(base_backoff * (2 ** (attempt - 1)))

    raise GatewayError(
        f"upstream provider request failed after {max_attempts} attempts"
    ) from last_error


class BaseForwarder(ABC):
    """Shared behavior for every provider-specific forwarder."""

    def __init__(
        self,
        *,
        default_api_key: str | None = None,
        timeout_seconds: float = 30.0,
        max_attempts: int = 3,
    ) -> None:
        self._default_api_key = default_api_key
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts

    def _resolve_api_key(self, envelope: RequestEnvelope) -> str:
        key = envelope.api_key or self._default_api_key
        if not key:
            raise GatewayError(
                f"{self._provider_name} API key is not configured"
            )
        return key

    @property
    @abstractmethod
    def _provider_name(self) -> str: ...

    @abstractmethod
    async def complete(self, envelope: RequestEnvelope) -> str: ...


class AnthropicForwarder(BaseForwarder):
    """Anthropic Messages API forwarder."""

    @property
    def _provider_name(self) -> str:
        return "Anthropic"

    async def complete(self, envelope: RequestEnvelope) -> str:
        api_key = self._resolve_api_key(envelope)
        url = envelope.base_url or "https://api.anthropic.com/v1/messages"
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
        response = await _post_with_retries(
            url=url,
            headers=headers,
            json=body,
            timeout_seconds=self._timeout_seconds,
            max_attempts=self._max_attempts,
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
        api_key = self._resolve_api_key(envelope)
        url = envelope.base_url or self._default_url
        headers: dict[str, str] = {"Authorization": f"Bearer {api_key}"}
        headers.update(self._extra_headers)
        body: dict[str, Any] = {
            "model": envelope.model,
            "temperature": float(envelope.temperature),
            "messages": list(envelope.messages),
        }
        if envelope.max_tokens is not None:
            body["max_tokens"] = envelope.max_tokens
        response = await _post_with_retries(
            url=url,
            headers=headers,
            json=body,
            timeout_seconds=self._timeout_seconds,
            max_attempts=self._max_attempts,
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
    def from_config(cls, config) -> "ForwarderRegistry":
        """Build the default registry using a :class:`GatewayConfig`."""
        return cls(
            anthropic=AnthropicForwarder(
                default_api_key=config.anthropic_api_key,
                timeout_seconds=config.request_timeout_seconds,
                max_attempts=config.max_attempts,
            ),
            openai=OpenAIForwarder(
                default_api_key=config.openai_api_key,
                timeout_seconds=config.request_timeout_seconds,
                max_attempts=config.max_attempts,
            ),
            openrouter=OpenRouterForwarder(
                default_api_key=config.openrouter_api_key,
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
