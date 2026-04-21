from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from ...models.settings import AppSettings

ChatMessage = Mapping[str, str]


class LLMProvider(Protocol):
    """Protocol for provider-specific LLM clients."""

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        temperature: float,
        max_tokens: int | None,
    ) -> str:
        ...


class LLMProviderConfig:
    """Immutable view of settings needed by LLM providers."""

    def __init__(self, settings: AppSettings, api_key: str | None = None) -> None:
        self._settings = settings
        self._api_key = api_key

    @property
    def model(self) -> str:
        return self._settings.llm_model

    @property
    def api_key(self) -> str | None:
        """Return the API key if explicitly provided, else None."""
        return self._api_key

    @property
    def extra(self) -> Mapping[str, Any]:
        return {}

