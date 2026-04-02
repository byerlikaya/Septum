from __future__ import annotations

from .openai_compatible import OpenAICompatibleProvider


class OpenAIProvider(OpenAICompatibleProvider):
    """OpenAI Chat Completions API implementation."""

    @property
    def _api_url(self) -> str:
        return "https://api.openai.com/v1/chat/completions"

    @property
    def _api_key_env_var(self) -> str:
        return "OPENAI_API_KEY"

    @property
    def _provider_display_name(self) -> str:
        return "OpenAI"
