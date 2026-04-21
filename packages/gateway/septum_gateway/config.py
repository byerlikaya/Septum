"""Gateway-side configuration.

The gateway runs in the internet-facing zone and owns its own secrets
(cloud LLM API keys, queue connection strings). Envelopes can still
carry an ``api_key`` for split deployments where the air-gapped side
holds the secret; when set that takes precedence, otherwise the
gateway falls back to its own environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class GatewayConfig:
    """Minimal configuration surface for the forwarder + consumer loop.

    ``request_topic`` and ``response_topic`` are the two queue topics
    that pair a request with its reply. The queue backend itself is
    constructed by the deployment code and passed into :class:`GatewayConsumer`
    separately — the config does not own the backend instance so that
    the same config can be reused across file- and Redis-backed setups.
    """

    request_topic: str = "septum.llm.requests"
    response_topic: str = "septum.llm.responses"
    audit_topic: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    openrouter_api_key: str | None = None
    request_timeout_seconds: float = 30.0
    max_attempts: int = 3

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        """Build a config entirely from ``SEPTUM_GATEWAY_*`` environment variables.

        Falls back to the historical ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` /
        ``OPENROUTER_API_KEY`` names so an operator pointing an existing
        env file at the gateway does not have to rename their secrets.
        """
        audit_topic = os.getenv("SEPTUM_GATEWAY_AUDIT_TOPIC")
        return cls(
            request_topic=os.getenv(
                "SEPTUM_GATEWAY_REQUEST_TOPIC", cls.request_topic
            ),
            response_topic=os.getenv(
                "SEPTUM_GATEWAY_RESPONSE_TOPIC", cls.response_topic
            ),
            audit_topic=audit_topic if audit_topic else None,
            anthropic_api_key=(
                os.getenv("SEPTUM_GATEWAY_ANTHROPIC_API_KEY")
                or os.getenv("ANTHROPIC_API_KEY")
            ),
            openai_api_key=(
                os.getenv("SEPTUM_GATEWAY_OPENAI_API_KEY")
                or os.getenv("OPENAI_API_KEY")
            ),
            openrouter_api_key=(
                os.getenv("SEPTUM_GATEWAY_OPENROUTER_API_KEY")
                or os.getenv("OPENROUTER_API_KEY")
            ),
            request_timeout_seconds=float(
                os.getenv("SEPTUM_GATEWAY_TIMEOUT_SECONDS", "30")
            ),
            max_attempts=int(os.getenv("SEPTUM_GATEWAY_MAX_ATTEMPTS", "3")),
        )

    def api_key_for(self, provider: str) -> str | None:
        """Return the stored default key for ``provider`` or ``None``.

        The envelope-carried ``api_key`` always wins; this is only
        consulted when the producer left the field empty.
        """
        provider = provider.strip().lower()
        if provider == "anthropic":
            return self.anthropic_api_key
        if provider == "openai":
            return self.openai_api_key
        if provider == "openrouter":
            return self.openrouter_api_key
        return None
