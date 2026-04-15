from __future__ import annotations

"""
MCP stdio server that exposes the septum-core PII masking pipeline.

The server uses the official ``mcp`` Python SDK's :class:`FastMCP`
helper so tool schemas are generated from type-annotated handler
signatures at registration time. Each handler delegates to a pure
function in :mod:`septum_mcp.tools`, which is also what the unit
tests exercise — the server itself is a thin transport layer.

Run locally::

    python -m septum_mcp.server

Or, once installed::

    septum-mcp

The engine is created lazily the first time a tool is invoked. This
keeps startup cost minimal (no transformer model is loaded until
actually needed), which matters when Claude Code spawns the server
for every workspace it opens.
"""

import logging
from typing import List, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from septum_core import SeptumCoreConfig, SeptumEngine

from . import tools as tool_impls
from .config import MCPConfig

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
    """

    def __init__(self, config: MCPConfig) -> None:
        self._config = config
        self._engine: Optional[SeptumEngine] = None

    @property
    def config(self) -> MCPConfig:
        return self._config

    def get(self) -> SeptumEngine:
        if self._engine is None:
            logger.info(
                "Initialising SeptumEngine with regulations=%s, ner=%s",
                self._config.regulations,
                self._config.use_ner_layer,
            )
            self._engine = _build_engine(self._config)
        return self._engine


def create_server(config: Optional[MCPConfig] = None) -> FastMCP:
    """Build a :class:`FastMCP` instance with all septum-mcp tools registered.

    Exposed as a module-level factory so tests and third-party
    integrators can spin up the server with a hand-built config
    instead of environment variables.
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


def main(argv: Optional[List[str]] = None) -> None:
    """Entry point for the ``septum-mcp`` console script.

    ``argv`` is accepted so tests can bypass ``sys.argv``. The server
    always speaks stdio transport because that is what Claude Code,
    Claude Desktop, and Cursor use for local MCP servers.
    """
    _ = argv
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    mcp = create_server()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
