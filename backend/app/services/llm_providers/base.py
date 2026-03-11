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

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    @property
    def model(self) -> str:
        return self._settings.llm_model

    @property
    def extra(self) -> Mapping[str, Any]:
        return {}

