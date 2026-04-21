from __future__ import annotations

from typing import Mapping

from .openai_compatible import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    """OpenRouter Chat Completions API implementation."""

    @property
    def _api_url(self) -> str:
        return "https://openrouter.ai/api/v1/chat/completions"

    @property
    def _api_key_env_var(self) -> str:
        return "OPENROUTER_API_KEY"

    @property
    def _provider_display_name(self) -> str:
        return "OpenRouter"

    @property
    def _extra_headers(self) -> Mapping[str, str]:
        return {
            "HTTP-Referer": "https://septum.local",
            "X-Title": "Septum",
        }
