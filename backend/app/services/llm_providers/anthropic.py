from __future__ import annotations

import os
from typing import Any, List, Sequence

from .base import ChatMessage, LLMProvider, LLMProviderConfig
from .http_client import post_with_retries
from ..llm_errors import LLMRouterError


class AnthropicProvider(LLMProvider):
    """Anthropic Messages API implementation."""

    def __init__(self, config: LLMProviderConfig) -> None:
        self._config = config

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        temperature: float,
        max_tokens: int | None,
    ) -> str:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise LLMRouterError("ANTHROPIC_API_KEY environment variable is not set.")

        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        body: dict[str, Any] = {
            "model": self._config.model,
            "temperature": float(temperature),
            "max_tokens": max_tokens or 1024,
            "messages": list(messages),
        }

        response = await post_with_retries(url=url, headers=headers, json=body)
        data = response.json()
        blocks = data.get("content") or []
        parts: List[str] = []
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                value = block.get("text")
                if isinstance(value, str):
                    parts.append(value)
        return "".join(parts)

