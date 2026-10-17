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
- ``SEPTUM_MCP_EXPOSE_SESSION_MAP`` — ``"true"`` registers the
  ``get_session_map`` debugging tool, which returns raw PII to the MCP
  client (and therefore to whatever model drives it). Defaults to
  ``"false"``. Honoured on stdio only; network transports never
  register the tool.
- ``SEPTUM_MCP_MAX_UNMASK_PLACEHOLDERS`` — per-session budget: the most
  distinct placeholders ``unmask_response`` may restore for one session
  across all of its calls. A call that would exceed it is refused
  without restoring anything; re-restoring a placeholder already
  restored is free. Positive integer; defaults to ``25``. Invalid
  values fall back to the default.
- ``SEPTUM_MCP_FILE_ROOT`` — directory ``scan_file`` is confined to.
  Optional on stdio; on network transports ``scan_file`` refuses every
  path while it is unset. Read once when the server is created: the
  directory must exist then, and it is pinned to the target it resolves
  to (a root behind a symlink is allowed), so a later swap of the root
  path refuses reads instead of moving the boundary.

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
from typing import List, Mapping

from septum_core.recognizers import (
    BUILTIN_REGULATION_IDS as DEFAULT_REGULATIONS,
)
from septum_core.recognizers import (
    parse_active_regulations_env,
)

DEFAULT_LANGUAGE = "en"
DEFAULT_SESSION_TTL_SECONDS = 3600.0
DEFAULT_MAX_UNMASK_PLACEHOLDERS = 25

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


def _parse_positive_int(value: str | None, default: int) -> int:
    if not value or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed >= 1 else default


def file_root_from_env(env: Mapping[str, str] | None = None) -> str | None:
    """Return ``SEPTUM_MCP_FILE_ROOT`` from ``env`` (defaults to ``os.environ``)."""
    source = env if env is not None else os.environ
    return source.get("SEPTUM_MCP_FILE_ROOT") or None


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

    ``file_root`` left as ``None`` is not final: :func:`septum_mcp.server.create_server`
    falls back to ``SEPTUM_MCP_FILE_ROOT`` from the process environment, so a
    hand-built config cannot silently drop a root the operator set.
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
    expose_session_map: bool = False
    max_unmask_placeholders: int = DEFAULT_MAX_UNMASK_PLACEHOLDERS
    file_root: str | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "MCPConfig":
        """Build an :class:`MCPConfig` from ``env`` (defaults to ``os.environ``)."""
        source = env if env is not None else os.environ
        return cls(
            regulations=parse_active_regulations_env(source.get("SEPTUM_REGULATIONS")),
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
            expose_session_map=_parse_bool(source.get("SEPTUM_MCP_EXPOSE_SESSION_MAP"), False),
            max_unmask_placeholders=_parse_positive_int(
                source.get("SEPTUM_MCP_MAX_UNMASK_PLACEHOLDERS"),
                DEFAULT_MAX_UNMASK_PLACEHOLDERS,
            ),
            file_root=file_root_from_env(source),
        )
