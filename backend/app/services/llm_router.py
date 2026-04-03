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

import logging
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, Iterable, List, Mapping, Sequence

from ..models.settings import AppSettings
from . import ollama_client
from .llm_errors import LLMRouterError
from .llm_providers.factory import build_provider
from .llm_providers.health import is_available, record_failure, record_success

logger = logging.getLogger(__name__)


ChatMessage = Dict[str, str]


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
        on_cloud_failure: Callable[[str, dict[str, Any]], Awaitable[Any]] | None = None,
    ) -> str:
        """Return the full completion text from the configured provider."""
        chunks: List[str] = []
        async for chunk in self.stream_chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            metadata=metadata,
            on_cloud_failure=on_cloud_failure,
        ):
            chunks.append(chunk)
        return "".join(chunks)

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
        temperature: float = 0.2,
        max_tokens: int | None = None,
        metadata: Mapping[str, Any] | None = None,  # noqa: ARG002
        on_cloud_failure: Callable[[str, dict[str, Any]], Awaitable[Any]] | None = None,
    ) -> AsyncGenerator[str, None]:
        """Yield completion text chunks from the configured provider.

        The granularity of chunks is an internal concern of this router. At
        present, providers are queried with non-streaming HTTP requests and
        the resulting text is sliced into modestly sized pieces to support
        SSE-style progressive rendering on the frontend.
        """
        if not messages:
            return

        provider_key = f"{self._provider}:{self._model}"

        if not is_available(provider_key):
            logger.warning(
                "Circuit breaker open for %s, skipping to Ollama fallback",
                provider_key,
            )
            text = await self._fallback_via_ollama(messages, temperature, max_tokens)
        else:
            try:
                provider = build_provider(self._settings)
                text = await provider.complete(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                record_success(provider_key)
            except Exception as exc:  # noqa: BLE001
                record_failure(provider_key)
                logger.error(
                    "Cloud LLM call failed, attempting Ollama fallback: provider=%s, model=%s, error=%s",
                    self._provider,
                    self._model,
                    exc,
                )
                if on_cloud_failure is not None:
                    try:
                        await on_cloud_failure(
                            f"Cloud LLM call failed, attempting Ollama fallback: provider={self._provider}, model={self._model}, error={exc}",
                            {"provider": self._provider, "model": self._model, "error": str(exc)},
                        )
                    except Exception:  # noqa: BLE001
                        pass
                text = await self._fallback_via_ollama(messages, temperature, max_tokens)

        for chunk in _chunk_text(text):
            yield chunk

    async def _fallback_via_ollama(
        self,
        messages: Sequence[ChatMessage],
        temperature: float,
        max_tokens: int | None,
    ) -> str:
        """Attempt to answer via local Ollama when cloud LLM calls fail.

        If Ollama is unavailable or returns an empty response, this method
        falls back to a deterministic local message so the user still gets a
        clear explanation of what happened.
        """
        # Build a simple chat-style prompt from the sanitised messages.
        # We do not alter or enrich the content here; all privacy handling
        # has already happened earlier in the pipeline.
        parts: List[str] = []
        for message in messages:
            role = (message.get("role") or "").strip().lower()
            content = message.get("content", "").strip()
            if not content:
                continue
            if role == "system":
                parts.append(f"[system]\n{content}\n")
            elif role == "assistant":
                parts.append(f"[assistant]\n{content}\n")
            else:
                parts.append(f"[user]\n{content}\n")

        prompt = "\n".join(parts).strip()
        if not prompt:
            return (
                "Cloud language model is currently unavailable and no prompt text "
                "was provided for local processing."
            )

        ollama_model = self._settings.ollama_chat_model
        try:
            response = await ollama_client.call_ollama_async(
                prompt=prompt,
                base_url=self._settings.ollama_base_url,
                model=ollama_model,
                timeout=60.0,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Ollama fallback call failed: model=%s error=%s", ollama_model, exc)
            response = ""

        if response and response.strip():
            return response

        # Final deterministic fallback if Ollama is not reachable or returns nothing.
        return (
            "Cloud language model and local Ollama fallback are both unavailable. "
            "Please check your network connection and local Ollama service."
        )

    # Provider-specific HTTP and response handling is delegated to strategy
    # objects created by ``build_provider``. The router remains responsible
    # only for orchestrating calls and handling provider-agnostic fallbacks.


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

