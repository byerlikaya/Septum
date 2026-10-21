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
from typing import (
    Any,
    AsyncGenerator,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Protocol,
    Sequence,
)

from ..env_flags import env_bool
from ..errors import public_error_text
from ..models.settings import AppSettings
from . import ollama_client
from .llm_errors import GATEWAY_NOT_CONFIGURED, LLMRouterError
from .llm_providers.factory import build_provider, validate_provider_config
from .llm_providers.health import is_available, record_failure, record_success

logger = logging.getLogger(__name__)


class GatewayClientProtocol(Protocol):
    """Producer-side surface the router calls when ``use_gateway`` is on."""

    async def complete(
        self,
        *,
        settings: AppSettings,
        messages: Sequence["ChatMessage"],
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str: ...


# Installed by the application lifespan when a queue is configured, so
# deployments without one never import ``septum_queue``.
_gateway_client_factory: Callable[[AppSettings], GatewayClientProtocol | None] | None = None

GATEWAY_NOT_CONFIGURED_MESSAGE = (
    "Gateway routing is enabled but no gateway queue is configured; set "
    "SEPTUM_QUEUE_URL or SEPTUM_QUEUE_DIR and restart the API."
)

# Providers that run on the local machine and therefore never cross the queue.
_LOCAL_PROVIDERS: frozenset[str] = frozenset({"ollama"})


def _gateway_forced_by_env() -> bool:
    """True when ``USE_GATEWAY_DEFAULT`` forces gateway routing over the stored setting."""
    return env_bool("USE_GATEWAY_DEFAULT", False)


def set_gateway_client_factory(
    factory: Callable[[AppSettings], GatewayClientProtocol | None] | None,
) -> None:
    """Install (or clear) the factory that returns the gateway client for a request."""
    global _gateway_client_factory
    _gateway_client_factory = factory


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

        validate_provider_config(self._settings)
        gateway = self._resolve_gateway_client() if self._routes_via_gateway() else None

        provider_key = f"{self._provider}:{self._model}"

        if not is_available(provider_key):
            logger.warning(
                "Circuit breaker open for %s, skipping to Ollama fallback",
                provider_key,
            )
            text = await self._fallback_via_ollama(messages, temperature, max_tokens)
        else:
            try:
                text = await self._dispatch_cloud_call(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    gateway=gateway,
                )
                record_success(provider_key)
            except Exception as exc:
                record_failure(provider_key)
                failure = _failure_summary(exc)
                logger.error(
                    "Cloud LLM call failed, attempting Ollama fallback: provider=%s, model=%s, error=%s",
                    self._provider,
                    self._model,
                    failure,
                )
                if on_cloud_failure is not None:
                    try:
                        await on_cloud_failure(
                            f"Cloud LLM call failed, attempting Ollama fallback: provider={self._provider}, model={self._model}, error={failure}",
                            {"provider": self._provider, "model": self._model, "error": failure},
                        )
                    except Exception as log_exc:
                        logger.warning("on_cloud_failure callback failed: %s", type(log_exc).__name__)
                text = await self._fallback_via_ollama(messages, temperature, max_tokens)

        for chunk in _chunk_text(text):
            yield chunk

    async def _dispatch_cloud_call(
        self,
        messages: Sequence[ChatMessage],
        temperature: float,
        max_tokens: int | None,
        gateway: GatewayClientProtocol | None = None,
    ) -> str:
        """Send the call through ``gateway`` when given, else to the provider directly.

        From the caller's point of view a gateway failure and a direct-call
        failure look the same, so both share the circuit breaker and the
        Ollama fallback.
        """
        if gateway is not None:
            return await gateway.complete(
                settings=self._settings,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        provider = build_provider(self._settings)
        return await provider.complete(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def _routes_via_gateway(self) -> bool:
        """True when gateway routing is on and the provider is not a local one.

        Routing is on when the stored ``use_gateway`` is set or
        ``USE_GATEWAY_DEFAULT`` forces it, so a stale stored ``False`` never
        lets a gateway deployment dial the cloud provider directly.
        """
        if self._provider in _LOCAL_PROVIDERS:
            return False
        return bool(self._settings.use_gateway) or _gateway_forced_by_env()

    def _resolve_gateway_client(self) -> GatewayClientProtocol:
        """Return the installed gateway client or refuse the call.

        Runs before the circuit breaker and outside the fallback, so a
        missing gateway is never answered by a direct cloud call or by
        Ollama. Factory exceptions propagate unchanged.
        """
        client = _gateway_client_factory(self._settings) if _gateway_client_factory else None
        if client is None:
            logger.error("Gateway routing is enabled but no gateway client is installed")
            raise LLMRouterError(GATEWAY_NOT_CONFIGURED_MESSAGE, code=GATEWAY_NOT_CONFIGURED)
        return client

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
        except Exception as exc:
            logger.error(
                "Ollama fallback call failed: model=%s error=%s", ollama_model, _failure_summary(exc)
            )
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


def _failure_summary(exc: BaseException) -> str:
    """Describe a provider failure by class and HTTP status only.

    Exception text from providers and HTTP clients can echo request content
    (masked prompts) or credentials, so it never reaches logs or the error table.
    """
    text = public_error_text(exc)
    for candidate in (exc, exc.__cause__):
        status = getattr(getattr(candidate, "response", None), "status_code", None)
        if status is None:
            status = getattr(candidate, "status_code", None)
        if isinstance(status, int):
            return f"{text} (status={status})"
    return text


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

