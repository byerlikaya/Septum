from __future__ import annotations

"""Base class for OpenAI-compatible Chat Completions API providers."""

import os
from abc import ABC, abstractmethod
from typing import Any, Mapping, Sequence

from .base import ChatMessage, LLMProviderConfig
from .http_client import post_with_retries
from ..llm_errors import LLMRouterError


class OpenAICompatibleProvider(ABC):
    """Base for providers that follow the OpenAI Chat Completions API contract.

    Subclasses override :meth:`_api_url`, :meth:`_api_key_env_var`, and
    optionally :meth:`_extra_headers` to specialise for a particular service
    while reusing the shared request/response logic.
    """

    def __init__(self, config: LLMProviderConfig) -> None:
        self._config = config

    @property
    @abstractmethod
    def _api_url(self) -> str:
        """Return the full URL for the chat completions endpoint."""
        ...

    @property
    @abstractmethod
    def _api_key_env_var(self) -> str:
        """Return the name of the environment variable holding the API key."""
        ...

    @property
    @abstractmethod
    def _provider_display_name(self) -> str:
        """Return a human-readable provider name for error messages."""
        ...

    @property
    def _extra_headers(self) -> Mapping[str, str]:
        """Return additional headers to include in requests.

        Subclasses may override this to add provider-specific headers.
        """
        return {}

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        temperature: float,
        max_tokens: int | None,
    ) -> str:
        """Send a chat completion request and return the response text."""
        api_key = self._config.api_key or os.getenv(self._api_key_env_var)
        if not api_key:
            raise LLMRouterError(
                f"{self._api_key_env_var} environment variable is not set."
            )

        headers: dict[str, str] = {
            "Authorization": f"Bearer {api_key}",
        }
        headers.update(self._extra_headers)

        body: dict[str, Any] = {
            "model": self._config.model,
            "temperature": float(temperature),
            "messages": list(messages),
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens

        response = await post_with_retries(
            url=self._api_url, headers=headers, json=body
        )
        data = response.json()

        choices = data.get("choices") or []
        if not choices:
            raise LLMRouterError(
                f"{self._provider_display_name} response did not contain any choices."
            )
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str):
            raise LLMRouterError(
                f"{self._provider_display_name} response did not contain text content."
            )
        return content
