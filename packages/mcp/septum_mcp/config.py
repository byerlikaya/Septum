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

HTTP-only variables (ignored in stdio mode, except that a malformed
``SEPTUM_MCP_HTTP_ALLOWED_HOSTS`` is a startup error in every mode):

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
- ``SEPTUM_MCP_HTTP_ALLOWED_HOSTS`` — comma-separated ``Host`` header
  allowlist, e.g. ``"mcp.internal:8765,mcp.internal:*"``. Each entry is
  ``host``, ``host:port`` or ``host:*`` (IPv6 hosts in brackets);
  entries are lowercased and normalised before matching. When
  set, DNS-rebinding protection is enabled: requests whose ``Host``
  header is not listed get 421, and a browser ``Origin`` header must be
  ``http://`` or ``https://`` plus one of the entries (else 403). When
  unset, loopback binds keep the SDK's localhost-only default and other
  binds accept any ``Host``. A malformed entry is a startup error in
  every transport mode.
"""

import ipaddress
import os
import re
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

_HOST_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_ALLOWED_HOST_PATTERN = re.compile(
    rf"(?P<name>{_HOST_LABEL}(?:\.{_HOST_LABEL})*|\[(?P<ipv6>[0-9A-Fa-f:.]+)\])"
    r"(?::(?P<port>\d{1,5}|\*))?"
)


class AllowedHostsError(ValueError):
    """Raised when ``SEPTUM_MCP_HTTP_ALLOWED_HOSTS`` holds a malformed entry."""


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


def _normalise_allowed_host(entry: str) -> str | None:
    """Return ``entry`` in the form the SDK's exact Host match expects, or ``None`` if malformed.

    The host is lowercased, an IPv6 host is rewritten to its bracketed
    compressed form and a numeric port loses leading zeros; ``*`` stays.
    """
    match = _ALLOWED_HOST_PATTERN.fullmatch(entry)
    if match is None:
        return None
    ipv6 = match.group("ipv6")
    if ipv6 is not None:
        try:
            name = f"[{ipaddress.IPv6Address(ipv6).compressed}]"
        except ValueError:
            return None
    else:
        name = match.group("name").lower()
    port = match.group("port")
    if port is None:
        return name
    if port == "*":
        return f"{name}:*"
    port_number = int(port)
    if not 1 <= port_number <= 65535:
        return None
    return f"{name}:{port_number}"


def parse_allowed_hosts(value: str | None) -> list[str]:
    """Parse a comma-separated ``Host`` allowlist into a list of entries.

    Whitespace is stripped and empty entries are dropped, so an unset or
    blank value yields ``[]`` (no allowlist). Each remaining entry must be
    ``host``, ``host:port`` or ``host:*``, where ``host`` is a dotted DNS
    name or IPv4-style address, or a bracketed IPv6 address. Entries are
    normalised (lowercase host, compressed IPv6, port without leading
    zeros) because the SDK matches the ``Host`` header exactly. Anything
    else (a scheme, a path, credentials, a bare ``*``, an out-of-range
    port) raises :class:`AllowedHostsError`. The message names the entry's
    position rather than echoing it; the entries themselves are never
    logged (the server logs only their count), so a mis-pasted secret does
    not reach the logs.
    """
    if not value:
        return []
    entries = [part.strip() for part in value.split(",")]
    entries = [entry for entry in entries if entry]
    normalised: list[str] = []
    for position, entry in enumerate(entries, start=1):
        result = _normalise_allowed_host(entry)
        if result is None:
            raise AllowedHostsError(
                f"allowed-hosts entry #{position} is invalid; "
                "expected host, host:port or host:* (IPv6 in brackets), "
                "comma-separated, without scheme or path."
            )
        normalised.append(result)
    return normalised


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
    http_allowed_hosts: list[str] = field(default_factory=list)
    expose_session_map: bool = False
    max_unmask_placeholders: int = DEFAULT_MAX_UNMASK_PLACEHOLDERS
    file_root: str | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "MCPConfig":
        """Build an :class:`MCPConfig` from ``env`` (defaults to ``os.environ``).

        Raises :class:`AllowedHostsError` when ``SEPTUM_MCP_HTTP_ALLOWED_HOSTS``
        holds a malformed entry; every other variable falls back to its default.
        """
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
            http_allowed_hosts=parse_allowed_hosts(source.get("SEPTUM_MCP_HTTP_ALLOWED_HOSTS")),
            expose_session_map=_parse_bool(source.get("SEPTUM_MCP_EXPOSE_SESSION_MAP"), False),
            max_unmask_placeholders=_parse_positive_int(
                source.get("SEPTUM_MCP_MAX_UNMASK_PLACEHOLDERS"),
                DEFAULT_MAX_UNMASK_PLACEHOLDERS,
            ),
            file_root=file_root_from_env(source),
        )
