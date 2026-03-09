from __future__ import annotations

"""
Provider-agnostic LLM routing for Septum.

This module exposes a small async API for sending chat-style prompts to a
cloud LLM provider while hiding provider-specific HTTP and response formats.
It is intentionally decoupled from FastAPI and SSE – the router yields text
chunks via an async generator which the HTTP layer can adapt to Server-Sent
Events or any other streaming transport.

Supported providers are selected via ``AppSettings.llm_provider``:

* ``anthropic``  → Anthropic Claude Messages API
* ``openai``     → OpenAI Chat Completions API
* ``openrouter`` → OpenRouter Chat Completions API (OpenAI-compatible)

All network calls use httpx and include simple retry logic with exponential
backoff, as required by the project guidelines. Prompts are expected to be
already sanitized before reaching this layer – this module never sees or logs
the anonymization map.
"""

import asyncio
import logging
import os
from typing import Any, AsyncGenerator, Dict, Iterable, List, Mapping, Sequence

import httpx

from ..models.settings import AppSettings

logger = logging.getLogger(__name__)


ChatMessage = Dict[str, str]


class LLMRouterError(RuntimeError):
    """Raised when an LLM provider call fails after retries."""


class LLMRouter:
    """High-level router for calling different LLM providers.

    The router is configured from :class:`AppSettings` and exposes two main
    methods:

    * :meth:`complete` – returns the full response text as a single string.
    * :meth:`stream_chat` – yields the response text incrementally, suitable
      for Server-Sent Events (SSE) in the HTTP layer.
    """

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._provider = (settings.llm_provider or "anthropic").strip().lower()
        self._model = settings.llm_model

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        temperature: float = 0.2,
        max_tokens: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        """Return the full completion text from the configured provider."""
        chunks: List[str] = []
        async for chunk in self.stream_chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            metadata=metadata,
        ):
            chunks.append(chunk)
        return "".join(chunks)

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
        temperature: float = 0.2,
        max_tokens: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AsyncGenerator[str, None]:
        """Yield completion text chunks from the configured provider.

        The granularity of chunks is an internal concern of this router. At
        present, providers are queried with non-streaming HTTP requests and
        the resulting text is sliced into modestly sized pieces to support
        SSE-style progressive rendering on the frontend.
        """
        if not messages:
            return

        try:
            if self._provider == "anthropic":
                text = await self._call_anthropic(messages, temperature, max_tokens)
            elif self._provider == "openai":
                text = await self._call_openai(messages, temperature, max_tokens)
            elif self._provider == "openrouter":
                text = await self._call_openrouter(messages, temperature, max_tokens)
            else:
                raise LLMRouterError(f"Unsupported LLM provider: {self._provider}")
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "LLM provider call failed: provider=%s, model=%s, error=%s",
                self._provider,
                self._model,
                exc,
            )
            raise

        for chunk in _chunk_text(text):
            yield chunk

    async def _call_anthropic(
        self,
        messages: Sequence[ChatMessage],
        temperature: float,
        max_tokens: int | None,
    ) -> str:
        """Call Anthropic Messages API and return the response text."""
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise LLMRouterError("ANTHROPIC_API_KEY environment variable is not set.")

        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        body: Dict[str, Any] = {
            "model": self._model,
            "temperature": float(temperature),
            "max_tokens": max_tokens or 1024,
            "messages": list(messages),
        }

        response = await _post_with_retries(url=url, headers=headers, json=body)
        data = response.json()

        # Anthropic returns content as a list of blocks; we concatenate text.
        blocks = data.get("content") or []
        parts: List[str] = []
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                value = block.get("text")
                if isinstance(value, str):
                    parts.append(value)
        return "".join(parts)

    async def _call_openai(
        self,
        messages: Sequence[ChatMessage],
        temperature: float,
        max_tokens: int | None,
    ) -> str:
        """Call OpenAI Chat Completions API and return the response text."""
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise LLMRouterError("OPENAI_API_KEY environment variable is not set.")

        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
        }
        body: Dict[str, Any] = {
            "model": self._model,
            "temperature": float(temperature),
            "messages": list(messages),
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens

        response = await _post_with_retries(url=url, headers=headers, json=body)
        data = response.json()

        choices = data.get("choices") or []
        if not choices:
            raise LLMRouterError("OpenAI response did not contain any choices.")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str):
            raise LLMRouterError("OpenAI response did not contain text content.")
        return content

    async def _call_openrouter(
        self,
        messages: Sequence[ChatMessage],
        temperature: float,
        max_tokens: int | None,
    ) -> str:
        """Call OpenRouter Chat Completions API and return the response text."""
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise LLMRouterError("OPENROUTER_API_KEY environment variable is not set.")

        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://septum.local",
            "X-Title": "Septum",
        }
        body: Dict[str, Any] = {
            "model": self._model,
            "temperature": float(temperature),
            "messages": list(messages),
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens

        response = await _post_with_retries(url=url, headers=headers, json=body)
        data = response.json()

        choices = data.get("choices") or []
        if not choices:
            raise LLMRouterError("OpenRouter response did not contain any choices.")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str):
            raise LLMRouterError("OpenRouter response did not contain text content.")
        return content


async def _post_with_retries(
    url: str,
    headers: Mapping[str, str],
    json: Mapping[str, Any],
    max_attempts: int = 3,
    base_backoff_seconds: float = 0.5,
) -> httpx.Response:
    """Send an HTTP POST request with simple exponential backoff.

    This helper is shared across all providers and never logs request bodies
    in order to avoid leaking prompt contents. Only metadata (URL, provider)
    should be logged by callers.
    """
    attempt = 0
    last_error: Exception | None = None

    while attempt < max_attempts:
        attempt += 1
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, headers=dict(headers), json=dict(json))
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            # HTTP-level error from provider (4xx/5xx). We do not log the request
            # body to avoid leaking prompt contents, but we *do* log status code
            # and a truncated version of the response body for easier debugging.
            last_error = exc
            status_code = exc.response.status_code
            body_preview: str
            try:
                # Provider error payloads should not contain user PII; still,
                # defensively truncate to keep logs small.
                raw_text = exc.response.text
                body_preview = raw_text[:512]
            except Exception:  # noqa: BLE001
                body_preview = "<unavailable>"

            logger.error(
                "LLM HTTP error while calling provider: url=%s status=%s body_preview=%s",
                url,
                status_code,
                body_preview,
            )

            if attempt >= max_attempts:
                break
            backoff = base_backoff_seconds * (2 ** (attempt - 1))
            await asyncio.sleep(backoff)
        except httpx.HTTPError as exc:
            # Transport-level error (DNS failure, timeout, etc.).
            last_error = exc
            logger.error("LLM transport error while calling provider: url=%s error=%s", url, exc)
            if attempt >= max_attempts:
                break
            backoff = base_backoff_seconds * (2 ** (attempt - 1))
            await asyncio.sleep(backoff)

    # Preserve the original exception chain but surface a message that includes
    # the last HTTP status code when available.
    if isinstance(last_error, httpx.HTTPStatusError):
        status_code = last_error.response.status_code
        raise LLMRouterError(
            f"LLM provider request failed after {max_attempts} attempts (status={status_code})."
        ) from last_error

    raise LLMRouterError(f"LLM provider request failed after {max_attempts} attempts.") from last_error


def _chunk_text(text: str, max_chunk_size: int = 256) -> Iterable[str]:
    """Split text into reasonably sized chunks for streaming.

    The chunking strategy is intentionally simple – fixed-size slices which
    avoid splitting surrogate pairs. This keeps the streaming layer predictable
    while remaining implementation-agnostic.
    """
    if not text:
        return []

    length = len(text)
    return (text[i : i + max_chunk_size] for i in range(0, length, max_chunk_size))

