from __future__ import annotations

"""
Environment-driven configuration for the septum-mcp server.

The server runs in one of three MCP transports:

- ``stdio`` (default) — launched as a subprocess by Claude Desktop,
  Cursor, ChatGPT Desktop, Windsurf, etc.; local, single-client, no auth.
- ``streamable-http`` — listens on HTTP, recommended for remote /
  container / browser deployments; add a bearer token for auth.
- ``sse`` — legacy HTTP+SSE transport, still supported for older
  clients that have not migrated to streamable-http yet.

Everything is read from environment variables at startup (so the stdio
launchers that cannot prompt the user interactively still work out of
the box) or overridden by CLI flags.

Environment variables:

- ``SEPTUM_REGULATIONS`` — comma-separated regulation pack ids, e.g.
  ``"gdpr,kvkk,hipaa"``. Unknown ids are silently ignored by the core
  composer. Defaults to **all 17 built-in packs** so users don't have
  to know which regulations apply to their data — the most-restrictive
  rule wins when multiple packs claim the same entity type.
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

HTTP-only variables (ignored in stdio mode):

- ``SEPTUM_MCP_TRANSPORT`` — ``"stdio"``, ``"streamable-http"`` or
  ``"sse"``. Defaults to ``"stdio"``.
- ``SEPTUM_MCP_HTTP_HOST`` — bind address. Defaults to ``127.0.0.1``
  (loopback only — put a reverse proxy / TLS terminator in front for
  public exposure).
- ``SEPTUM_MCP_HTTP_PORT`` — TCP port. Defaults to ``8765``.
- ``SEPTUM_MCP_HTTP_TOKEN`` — bearer token for ``Authorization:
  Bearer <token>`` header validation. When unset, HTTP transport runs
  **without auth**; intended only for localhost development.
- ``SEPTUM_MCP_HTTP_MOUNT_PATH`` — URL path prefix for the MCP
  endpoint. Defaults to the SDK default (``/mcp`` for streamable-http,
  ``/sse`` for sse).
"""

import os
from dataclasses import dataclass, field
from typing import List

from septum_core.recognizers import BUILTIN_REGULATION_IDS as DEFAULT_REGULATIONS

DEFAULT_LANGUAGE = "en"
DEFAULT_SESSION_TTL_SECONDS = 3600.0

DEFAULT_TRANSPORT = "stdio"
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8765

VALID_TRANSPORTS = frozenset({"stdio", "sse", "streamable-http"})


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


def _parse_transport(value: str | None) -> str:
    """Normalise a transport string, falling back to the default on garbage."""
    if not value:
        return DEFAULT_TRANSPORT
    normalised = value.strip().lower()
    if normalised not in VALID_TRANSPORTS:
        return DEFAULT_TRANSPORT
    return normalised


def _parse_port(value: str | None, default: int) -> int:
    if not value or not value.strip():
        return default
    try:
        port = int(value)
    except ValueError:
        return default
    if port < 1 or port > 65535:
        return default
    return port


def _parse_optional_str(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


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
    transport: str = DEFAULT_TRANSPORT
    http_host: str = DEFAULT_HTTP_HOST
    http_port: int = DEFAULT_HTTP_PORT
    http_token: str | None = None
    http_mount_path: str | None = None

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
            transport=_parse_transport(source.get("SEPTUM_MCP_TRANSPORT")),
            http_host=(source.get("SEPTUM_MCP_HTTP_HOST") or DEFAULT_HTTP_HOST).strip() or DEFAULT_HTTP_HOST,
            http_port=_parse_port(source.get("SEPTUM_MCP_HTTP_PORT"), DEFAULT_HTTP_PORT),
            http_token=_parse_optional_str(source.get("SEPTUM_MCP_HTTP_TOKEN")),
            http_mount_path=_parse_optional_str(source.get("SEPTUM_MCP_HTTP_MOUNT_PATH")),
        )
