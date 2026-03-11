from __future__ import annotations

import os
from typing import Any, Sequence

from .base import ChatMessage, LLMProvider, LLMProviderConfig
from .http_client import post_with_retries
from ..llm_errors import LLMRouterError


class OpenAIProvider(LLMProvider):
    """OpenAI Chat Completions API implementation."""

    def __init__(self, config: LLMProviderConfig) -> None:
        self._config = config

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        temperature: float,
        max_tokens: int | None,
    ) -> str:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise LLMRouterError("OPENAI_API_KEY environment variable is not set.")

        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
        }
        body: dict[str, Any] = {
            "model": self._config.model,
            "temperature": float(temperature),
            "messages": list(messages),
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens

        response = await post_with_retries(url=url, headers=headers, json=body)
        data = response.json()

        choices = data.get("choices") or []
        if not choices:
            raise LLMRouterError("OpenAI response did not contain any choices.")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str):
            raise LLMRouterError("OpenAI response did not contain text content.")
        return content


