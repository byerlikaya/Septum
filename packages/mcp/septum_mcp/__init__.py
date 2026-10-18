"""Septum MCP server package.

Exposes a :class:`MCPServer` server that wraps :mod:`septum_core` and
surfaces five tools (``mask_text``, ``unmask_response``, ``detect_pii``,
``scan_file``, ``list_regulations``) to any MCP client, plus the opt-in
``get_session_map`` debugging tool. The server runs over stdio and is intended to be launched
directly by Claude Code, Claude Desktop, Cursor, or any other MCP-aware
tool.
"""

from __future__ import annotations

from .config import MCPConfig
from .server import SERVER_INSTRUCTIONS, create_server, main

__all__ = ["MCPConfig", "create_server", "main", "SERVER_INSTRUCTIONS"]
__version__ = "0.1.0"
