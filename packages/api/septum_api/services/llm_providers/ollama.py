from __future__ import annotations

"""Local Ollama LLM provider.

When ``llm_provider`` is set to ``ollama`` in :class:`AppSettings`, the router
calls a local Ollama instance via :func:`ollama_client.call_ollama_async`
instead of a cloud API. No API key is required.
"""

from typing import Sequence

from .. import ollama_client
from ..llm_errors import LLMRouterError
from .base import ChatMessage, LLMProvider, LLMProviderConfig


class OllamaProvider(LLMProvider):
    """Provider that delegates chat completions to a local Ollama instance."""

    def __init__(self, config: LLMProviderConfig) -> None:
        self._config = config

    def validate_config(self) -> None:
        ollama_client.require_allowed_ollama_url(self._config.settings.ollama_base_url)

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        temperature: float,
        max_tokens: int | None,
    ) -> str:
        """Render the messages into a single prompt and call Ollama."""
        parts: list[str] = []
        for message in messages:
            role = (message.get("role") or "").strip().lower()
            content = (message.get("content") or "").strip()
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
            raise LLMRouterError("Ollama provider received an empty prompt.")

        settings = self._config.settings
        response = await ollama_client.call_ollama_async(
            prompt=prompt,
            base_url=ollama_client.effective_ollama_base_url(settings.ollama_base_url),
            model=settings.ollama_chat_model or settings.llm_model,
            timeout=120.0,
        )
        if not response or not response.strip():
            raise LLMRouterError(
                "Ollama returned an empty response. "
                "Check that the model is pulled and the server is reachable."
            )
        return response
