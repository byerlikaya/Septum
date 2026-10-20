from __future__ import annotations

OLLAMA_URL_NOT_ALLOWED_CODE = "ollama_url_not_allowed"
LLM_UNAVAILABLE = "llm_unavailable"


class LLMRouterError(RuntimeError):
    """Raised when an LLM provider call or routing step fails.

    ``code`` is an optional machine-readable reason surfaced to clients.
    """

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code

