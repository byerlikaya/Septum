from __future__ import annotations

OLLAMA_URL_NOT_ALLOWED_CODE = "ollama_url_not_allowed"
LLM_UNAVAILABLE = "llm_unavailable"
GATEWAY_NOT_CONFIGURED = "gateway_not_configured"


class LLMRouterError(RuntimeError):
    """Raised when an LLM provider call or routing step fails.

    ``code`` is an optional machine-readable reason surfaced to clients.
    """

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code



class GatewayStartupError(RuntimeError):
    """The gateway queue configuration cannot work; the API refuses to start.

    Messages name environment variables only, never their values, because a
    queue URL can carry a password.
    """
