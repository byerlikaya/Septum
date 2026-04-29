from __future__ import annotations

"""HTTP-transport hardening regression tests.

Pins two privacy-critical invariants:

* ``get_session_map`` must NOT be registered on non-stdio transports —
  the tool returns the raw {original → placeholder} map for a session,
  which is fine for a local stdio subprocess but a PII-leak surface as
  soon as a network transport is involved.
* ``_run_http`` must REFUSE to start when the operator binds to a
  non-loopback host without a bearer token.
"""

import os

import pytest

from septum_mcp.config import MCPConfig
from septum_mcp.server import _run_http, create_server


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch) -> None:
    monkeypatch.setenv("SEPTUM_REGULATIONS", "gdpr")
    monkeypatch.setenv("SEPTUM_USE_NER", "false")


def _registered_tool_names(server) -> set[str]:
    return set(server._tool_manager._tools.keys())


def test_session_map_registered_on_stdio() -> None:
    server = create_server(MCPConfig.from_env(), expose_session_map=True)
    assert "get_session_map" in _registered_tool_names(server)


def test_session_map_omitted_on_http() -> None:
    server = create_server(MCPConfig.from_env(), expose_session_map=False)
    assert "get_session_map" not in _registered_tool_names(server)


def test_run_http_refuses_unauth_non_loopback() -> None:
    server = create_server(MCPConfig.from_env(), expose_session_map=False)
    with pytest.raises(RuntimeError, match="bearer token"):
        _run_http(
            server,
            transport="streamable-http",
            host="0.0.0.0",
            port=8000,
            token=None,
            mount_path=None,
        )
