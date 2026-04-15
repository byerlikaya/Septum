from __future__ import annotations

"""
Environment-driven configuration for the septum-mcp server.

The server is launched as an MCP stdio subprocess (by Claude Code,
Claude Desktop, Cursor, …) and therefore cannot prompt the user for
configuration interactively. Everything is read from environment
variables at startup, with sensible defaults that make the server
usable out of the box for a single-developer workflow.

Environment variables:

- ``SEPTUM_REGULATIONS`` — comma-separated regulation pack ids, e.g.
  ``"gdpr,kvkk,hipaa"``. Unknown ids are silently ignored by the core
  composer. Defaults to ``"gdpr"``.
- ``SEPTUM_LANGUAGE`` — default ISO 639-1 language hint passed to the
  detector when callers don't specify one. Defaults to ``"en"``.
- ``SEPTUM_USE_NER`` — ``"true"`` / ``"false"``. When false, the
  transformer-based NER layer is skipped entirely, which keeps the
  process light and avoids pulling ``torch`` / ``transformers`` at
  import time. Defaults to ``"true"``.
- ``SEPTUM_USE_PRESIDIO`` — toggles the Presidio regex/recognizer
  layer. Defaults to ``"true"``.
- ``SEPTUM_SESSION_TTL`` — session retention in seconds. ``0`` or
  negative values disable TTL eviction. Defaults to ``3600``.
"""

import os
from dataclasses import dataclass, field
from typing import List

DEFAULT_REGULATIONS = ("gdpr",)
DEFAULT_LANGUAGE = "en"
DEFAULT_SESSION_TTL_SECONDS = 3600.0


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_float(value: str | None, default: float) -> float:
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _parse_regulation_list(value: str | None) -> List[str]:
    if not value:
        return list(DEFAULT_REGULATIONS)
    items = [item.strip().lower() for item in value.split(",")]
    return [item for item in items if item]


@dataclass
class MCPConfig:
    """Runtime configuration for the septum-mcp server.

    Populated from environment variables by :meth:`from_env`, but can
    also be constructed directly in tests to avoid mutating the process
    environment.
    """

    regulations: List[str] = field(default_factory=lambda: list(DEFAULT_REGULATIONS))
    language: str = DEFAULT_LANGUAGE
    use_ner_layer: bool = True
    use_presidio_layer: bool = True
    session_ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "MCPConfig":
        """Build an :class:`MCPConfig` from ``env`` (defaults to ``os.environ``)."""
        source = env if env is not None else os.environ
        return cls(
            regulations=_parse_regulation_list(source.get("SEPTUM_REGULATIONS")),
            language=(source.get("SEPTUM_LANGUAGE") or DEFAULT_LANGUAGE).strip(),
            use_ner_layer=_parse_bool(source.get("SEPTUM_USE_NER"), True),
            use_presidio_layer=_parse_bool(source.get("SEPTUM_USE_PRESIDIO"), True),
            session_ttl_seconds=_parse_float(
                source.get("SEPTUM_SESSION_TTL"), DEFAULT_SESSION_TTL_SECONDS
            ),
        )
