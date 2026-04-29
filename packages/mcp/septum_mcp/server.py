from __future__ import annotations

"""
MCP server that exposes the septum-core PII masking pipeline.

The server uses the official ``mcp`` Python SDK's :class:`FastMCP`
helper so tool schemas are generated from type-annotated handler
signatures at registration time. Each handler delegates to a pure
function in :mod:`septum_mcp.tools`, which is also what the unit
tests exercise — the server itself is a thin transport layer.

Three transports are supported:

- ``stdio`` (default) — for Claude Desktop, Cursor, ChatGPT Desktop,
  and other subprocess-launching MCP clients.
- ``streamable-http`` — modern HTTP transport for container / remote /
  browser deployments. Gated behind a bearer token when
  ``--token`` / ``SEPTUM_MCP_HTTP_TOKEN`` is set.
- ``sse`` — legacy HTTP+SSE transport, kept for clients that have not
  migrated to streamable-http yet.

Run locally::

    python -m septum_mcp.server                 # stdio
    septum-mcp                                   # stdio (installed)
    septum-mcp --transport streamable-http \\
      --port 8765 --token <secret>               # remote HTTP

The engine is created lazily the first time a tool is invoked. This
keeps startup cost minimal (no transformer model is loaded until
actually needed), which matters when Claude Code spawns the server
for every workspace it opens.
"""

import argparse
import logging
import threading
from typing import List, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from septum_core import SeptumCoreConfig, SeptumEngine

from . import tools as tool_impls
from .auth import BearerTokenMiddleware
from .config import VALID_TRANSPORTS, MCPConfig

logger = logging.getLogger("septum_mcp.server")

SERVER_INSTRUCTIONS = (
    "Septum MCP server. Exposes privacy-first PII masking tools backed by "
    "septum-core. All detection runs locally — raw PII never leaves the "
    "machine. Typical workflow: call mask_text before sending content to a "
    "remote LLM, then call unmask_response on the reply using the returned "
    "session_id. Use detect_pii for read-only scans, scan_file for local "
    "documents, list_regulations to see available packs, and get_session_map "
    "for debugging."
)


def _build_engine(config: MCPConfig) -> SeptumEngine:
    """Construct a :class:`SeptumEngine` from the given :class:`MCPConfig`."""
    core_config = SeptumCoreConfig(
        use_presidio_layer=config.use_presidio_layer,
        use_ner_layer=config.use_ner_layer,
    )
    return SeptumEngine(
        regulations=list(config.regulations),
        config=core_config,
        session_ttl_seconds=config.session_ttl_seconds,
    )


class _EngineHolder:
    """Lazy, single-instance holder for the core engine.

    Creating the engine eagerly at import time would trigger heavy
    imports (spaCy pipeline, optional transformer weights) the moment
    Claude Code launches the server. Deferring construction to the
    first tool call keeps idle cost near zero.

    The lazy initialisation is guarded by a ``threading.Lock``: FastMCP
    runs sync tools on a thread pool, so two concurrent first-call
    requests over the streamable-http transport could each see
    ``self._engine is None`` and call ``_build_engine`` twice. The
    second engine would then silently overwrite the first, orphaning
    any sessions registered against the original.
    """

    def __init__(self, config: MCPConfig) -> None:
        self._config = config
        self._engine: Optional[SeptumEngine] = None
        self._init_lock = threading.Lock()

    @property
    def config(self) -> MCPConfig:
        return self._config

    def get(self) -> SeptumEngine:
        if self._engine is not None:
            return self._engine
        with self._init_lock:
            # Double-checked: another thread may have built the engine
            # while we were waiting for the lock.
            if self._engine is not None:
                return self._engine
            logger.info(
                "Initialising SeptumEngine with regulations=%s, ner=%s",
                self._config.regulations,
                self._config.use_ner_layer,
            )
            self._engine = _build_engine(self._config)
            return self._engine


def create_server(
    config: Optional[MCPConfig] = None,
    *,
    expose_session_map: bool = True,
) -> FastMCP:
    """Build a :class:`FastMCP` instance with all septum-mcp tools registered.

    ``expose_session_map`` controls whether ``get_session_map`` (which
    returns raw PII for debugging) is registered. Callers should pass
    ``False`` for non-stdio transports — the tool is intended for
    local-only debugging and exposing it over a network transport
    leaks raw originals to any client with a valid bearer token.
    """
    cfg = config or MCPConfig.from_env()
    mcp = FastMCP(name="septum-mcp", instructions=SERVER_INSTRUCTIONS)
    holder = _EngineHolder(cfg)

    def _unwrap(envelope: dict) -> dict:
        """Turn a ``{ok: False}`` envelope into a :class:`ToolError`.

        Successful envelopes return their ``data`` payload so MCP
        clients see a clean JSON object instead of an extra ``ok``
        wrapper. Failures raise ``ToolError``, which FastMCP maps to
        ``isError=True`` on the outgoing tool result.
        """
        if not envelope.get("ok", False):
            raise ToolError(envelope.get("error", "septum-mcp tool failed"))
        return envelope.get("data", {})

    @mcp.tool(
        name="mask_text",
        description=(
            "Detect and mask PII in the given text using the configured "
            "regulations. Returns masked text plus a session_id that "
            "unmask_response can use to restore the originals."
        ),
    )
    def mask_text(text: str, language: str = "en") -> dict:
        return _unwrap(
            tool_impls.mask_text(holder.get(), text=text, language=language)
        )

    @mcp.tool(
        name="unmask_response",
        description=(
            "Restore original PII values inside an LLM response by "
            "replacing placeholders with the values recorded in the "
            "session anonymization map."
        ),
    )
    def unmask_response(text: str, session_id: str) -> dict:
        return _unwrap(
            tool_impls.unmask_response(
                holder.get(), text=text, session_id=session_id
            )
        )

    @mcp.tool(
        name="detect_pii",
        description=(
            "Scan text for PII entities without retaining a session. "
            "Returns the detected entities with type, position, "
            "placeholder, and confidence score."
        ),
    )
    def detect_pii(text: str, language: str = "en") -> dict:
        return _unwrap(
            tool_impls.detect_pii(holder.get(), text=text, language=language)
        )

    @mcp.tool(
        name="scan_file",
        description=(
            "Read a local file (.txt, .md, .csv, .json, .pdf, .docx) and "
            "scan its text content for PII. Set mask=true to also "
            "receive the masked text and a session id."
        ),
    )
    def scan_file(file_path: str, mask: bool = False, language: str = "en") -> dict:
        return _unwrap(
            tool_impls.scan_file(
                holder.get(),
                file_path=file_path,
                mask=mask,
                language=language,
            )
        )

    @mcp.tool(
        name="list_regulations",
        description=(
            "List the built-in regulation packs with their declared "
            "entity types. Each pack is marked is_active when it is in "
            "the server's current regulation set."
        ),
    )
    def list_regulations() -> dict:
        return _unwrap(
            tool_impls.list_regulations(active_regulations=cfg.regulations)
        )

    if expose_session_map:
        @mcp.tool(
            name="get_session_map",
            description=(
                "Return the {original -> placeholder} map for a session. "
                "Intended strictly for local debugging; the values contain "
                "raw PII and must not be forwarded to any remote system."
            ),
        )
        def get_session_map(session_id: str) -> dict:
            return _unwrap(
                tool_impls.get_session_map(holder.get(), session_id=session_id)
            )

    # Keep a reference on the server for tests that want to introspect.
    mcp._septum_holder = holder  # type: ignore[attr-defined]
    return mcp


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="septum-mcp",
        description="Septum MCP server — PII masking tools over stdio, streamable-http, or SSE.",
    )
    parser.add_argument(
        "--transport",
        choices=sorted(VALID_TRANSPORTS),
        default=None,
        help="Transport to bind. Defaults to SEPTUM_MCP_TRANSPORT or 'stdio'.",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="HTTP bind address (streamable-http / sse only). Defaults to SEPTUM_MCP_HTTP_HOST or 127.0.0.1.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="HTTP TCP port (streamable-http / sse only). Defaults to SEPTUM_MCP_HTTP_PORT or 8765.",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Bearer token required on 'Authorization: Bearer <token>'. Defaults to SEPTUM_MCP_HTTP_TOKEN. When unset, HTTP transport runs without auth — localhost only.",
    )
    parser.add_argument(
        "--mount-path",
        default=None,
        help="URL path prefix for the MCP endpoint (streamable-http / sse only). Defaults to the SDK default.",
    )
    return parser


def _run_http(mcp: FastMCP, *, transport: str, host: str, port: int, token: str | None, mount_path: str | None) -> None:
    """Serve the FastMCP ASGI app via uvicorn with a bearer-token gate."""
    # Import uvicorn lazily so stdio-only deployments don't pay the cost
    # of loading the HTTP stack at import time.
    import uvicorn  # type: ignore[import-not-found]

    mcp.settings.host = host
    mcp.settings.port = port
    if mount_path:
        if transport == "streamable-http":
            mcp.settings.streamable_http_path = mount_path
        else:
            mcp.settings.sse_path = mount_path

    app = (
        mcp.streamable_http_app()
        if transport == "streamable-http"
        else mcp.sse_app()
    )
    app = BearerTokenMiddleware(app, token=token)

    logger.info(
        "Starting septum-mcp transport=%s host=%s port=%s auth=%s",
        transport,
        host,
        port,
        "enabled" if token else "disabled",
    )
    if token is None and host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(
            f"Refusing to start septum-mcp HTTP transport on {host} without a "
            "bearer token. Set --token / SEPTUM_MCP_HTTP_TOKEN, or bind to "
            "127.0.0.1 / localhost / ::1 for loopback-only use."
        )

    uvicorn.run(app, host=host, port=port, log_level="info")


def main(argv: Optional[List[str]] = None) -> None:
    """Entry point for the ``septum-mcp`` console script.

    ``argv`` is accepted so tests can bypass ``sys.argv``. Transport is
    selected by (1) ``--transport`` CLI flag, (2) ``SEPTUM_MCP_TRANSPORT``
    env var, (3) default ``stdio``.
    """
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    cfg = MCPConfig.from_env()
    transport = args.transport or cfg.transport
    host = args.host or cfg.http_host
    port = args.port if args.port is not None else cfg.http_port
    token = args.token or cfg.http_token
    mount_path = args.mount_path or cfg.http_mount_path

    # get_session_map returns raw PII; only expose it on stdio (the only
    # transport that runs as a subprocess of a trusted local client).
    mcp = create_server(cfg, expose_session_map=(transport == "stdio"))

    if transport == "stdio":
        mcp.run(transport="stdio")
        return

    _run_http(
        mcp,
        transport=transport,
        host=host,
        port=port,
        token=token,
        mount_path=mount_path,
    )


if __name__ == "__main__":
    main()
