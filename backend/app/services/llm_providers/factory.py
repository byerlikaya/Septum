from __future__ import annotations

from typing import Mapping

from ...models.settings import AppSettings
from ..llm_errors import LLMRouterError
from .anthropic import AnthropicProvider
from .base import LLMProvider, LLMProviderConfig
from .openai import OpenAIProvider
from .openrouter import OpenRouterProvider

_PROVIDER_MAP: Mapping[str, type[LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "openrouter": OpenRouterProvider,
}


_API_KEY_FIELDS: Mapping[str, str] = {
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
    "openrouter": "openrouter_api_key",
}


def build_provider(settings: AppSettings) -> LLMProvider:
    """Construct a concrete provider instance from application settings."""
    provider_key = (settings.llm_provider or "anthropic").strip().lower()
    provider_cls = _PROVIDER_MAP.get(provider_key)
    if provider_cls is None:
        raise LLMRouterError(f"Unsupported LLM provider: {provider_key}")
    field = _API_KEY_FIELDS.get(provider_key)
    api_key = getattr(settings, field, None) if field else None
    config = LLMProviderConfig(settings, api_key=api_key or None)
    return provider_cls(config)

