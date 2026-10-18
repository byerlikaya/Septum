from __future__ import annotations

"""HTTP-transport hardening regression tests.

Pins two privacy-critical invariants:

* ``get_session_map`` returns the raw {original → placeholder} map for a
  session, so it is never registered by default on any transport, is
  enabled only by ``SEPTUM_MCP_EXPOSE_SESSION_MAP=true``, and even then
  only over stdio.
* ``_run_http`` must REFUSE to start when the operator binds to a
  non-loopback host without a bearer token.
"""

import pytest
from septum_mcp import server as server_module
from septum_mcp.config import MCPConfig
from septum_mcp.server import SERVER_INSTRUCTIONS, _run_http, create_server


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch) -> None:
    monkeypatch.setenv("SEPTUM_REGULATIONS", "gdpr")
    monkeypatch.setenv("SEPTUM_USE_NER", "false")


def _registered_tool_names(server) -> set[str]:
    return set(server._tool_manager._tools.keys())


def test_session_map_omitted_by_default() -> None:
    server = create_server(MCPConfig.from_env())
    assert "get_session_map" not in _registered_tool_names(server)


def test_session_map_registered_when_env_opts_in(monkeypatch) -> None:
    monkeypatch.setenv("SEPTUM_MCP_EXPOSE_SESSION_MAP", "true")
    server = create_server(MCPConfig.from_env())
    assert "get_session_map" in _registered_tool_names(server)


@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
def test_create_server_never_registers_session_map_on_network_transport(transport) -> None:
    server = create_server(
        MCPConfig(regulations=["gdpr"], use_ner_layer=False, transport=transport),
        expose_session_map=True,
    )
    assert "get_session_map" not in _registered_tool_names(server)


def test_create_server_registers_session_map_on_stdio_when_requested() -> None:
    server = create_server(
        MCPConfig(regulations=["gdpr"], use_ner_layer=False, transport="stdio"),
        expose_session_map=True,
    )
    assert "get_session_map" in _registered_tool_names(server)


def test_file_root_is_read_from_config_not_process_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SEPTUM_MCP_FILE_ROOT", str(tmp_path / "from_env"))
    assert MCPConfig.from_env({}).file_root is None
    assert MCPConfig.from_env({"SEPTUM_MCP_FILE_ROOT": str(tmp_path)}).file_root == str(tmp_path)


def test_instructions_do_not_advertise_session_map() -> None:
    assert "get_session_map" not in SERVER_INSTRUCTIONS
    assert "session_map" not in create_server(MCPConfig.from_env()).instructions


def _capture_main_exposure(monkeypatch, transport: str) -> bool:
    """Run ``main`` for ``transport`` with I/O stubbed; return whether it registered get_session_map."""
    captured: dict[str, bool] = {}
    real_create_server = server_module.create_server

    class _StubServer:
        def run(self, **_kwargs) -> None:
            return None

    def _spy_create_server(cfg, **kwargs):
        captured["expose"] = "get_session_map" in _registered_tool_names(
            real_create_server(cfg, **kwargs)
        )
        return _StubServer()

    monkeypatch.setattr(server_module, "create_server", _spy_create_server)
    monkeypatch.setattr(server_module, "_run_http", lambda *a, **k: None)
    server_module.main(["--transport", transport])
    return captured["expose"]


@pytest.mark.parametrize("transport", ["stdio", "streamable-http", "sse"])
def test_main_never_exposes_session_map_by_default(monkeypatch, transport) -> None:
    monkeypatch.delenv("SEPTUM_MCP_EXPOSE_SESSION_MAP", raising=False)
    assert _capture_main_exposure(monkeypatch, transport) is False


def test_main_exposes_session_map_on_stdio_when_opted_in(monkeypatch) -> None:
    monkeypatch.setenv("SEPTUM_MCP_EXPOSE_SESSION_MAP", "true")
    assert _capture_main_exposure(monkeypatch, "stdio") is True


@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
def test_main_ignores_opt_in_on_network_transports(monkeypatch, transport) -> None:
    monkeypatch.setenv("SEPTUM_MCP_EXPOSE_SESSION_MAP", "true")
    assert _capture_main_exposure(monkeypatch, transport) is False


def test_run_http_refuses_unauth_non_loopback() -> None:
    server = create_server(MCPConfig.from_env())
    with pytest.raises(RuntimeError, match="bearer token"):
        _run_http(
            server,
            transport="streamable-http",
            host="0.0.0.0",
            port=8000,
            token=None,
            mount_path=None,
        )


def _scan_file_tool(server):
    return server._tool_manager._tools["scan_file"].fn


@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
def test_network_transport_without_root_refuses_scan_file(
    tmp_path, monkeypatch, transport
) -> None:
    from mcp.server.mcpserver.exceptions import ToolError

    monkeypatch.delenv("SEPTUM_MCP_FILE_ROOT", raising=False)
    target = tmp_path / "ok.txt"
    target.write_text("data", encoding="utf-8")
    server = create_server(MCPConfig(regulations=["gdpr"], use_ner_layer=False, transport=transport))

    with pytest.raises(ToolError, match="SEPTUM_MCP_FILE_ROOT"):
        _scan_file_tool(server)(file_path=str(target))


def test_stdio_without_root_still_scans(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("SEPTUM_MCP_FILE_ROOT", raising=False)
    target = tmp_path / "ok.txt"
    target.write_text("data", encoding="utf-8")
    server = create_server(MCPConfig(regulations=["gdpr"], use_ner_layer=False, transport="stdio"))

    assert _scan_file_tool(server)(file_path=str(target))["format"]


def _pinned_root_fixture(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "ok.txt").write_text("data", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "ok.txt").write_text("jane@example.com", encoding="utf-8")
    server = create_server(
        MCPConfig(
            regulations=["gdpr"],
            use_ner_layer=False,
            transport="streamable-http",
            file_root=str(root),
        )
    )
    return server, root, outside


def test_hand_built_config_without_root_falls_back_to_env(tmp_path, monkeypatch) -> None:
    from mcp.server.mcpserver.exceptions import ToolError

    root = tmp_path / "root"
    root.mkdir()
    (root / "ok.txt").write_text("data", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("jane@example.com", encoding="utf-8")
    monkeypatch.setenv("SEPTUM_MCP_FILE_ROOT", str(root))
    server = create_server(MCPConfig(regulations=["gdpr"], use_ner_layer=False, transport="stdio"))

    assert _scan_file_tool(server)(file_path=str(root / "ok.txt"))["format"] == "txt"
    with pytest.raises(ToolError):
        _scan_file_tool(server)(file_path=str(outside))


def test_pinned_root_serves_reads_inside_it(tmp_path) -> None:
    server, root, _ = _pinned_root_fixture(tmp_path)

    assert _scan_file_tool(server)(file_path=str(root / "ok.txt"))["format"] == "txt"


def test_root_swapped_for_symlink_after_startup_is_refused(tmp_path) -> None:
    from mcp.server.mcpserver.exceptions import ToolError
    from septum_mcp import tools

    server, root, outside = _pinned_root_fixture(tmp_path)
    root.rename(tmp_path / "root_moved")
    root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ToolError) as excinfo:
        _scan_file_tool(server)(file_path=str(root / "ok.txt"))
    assert str(excinfo.value) == tools._SCAN_PATH_UNAVAILABLE
    assert "jane@example.com" not in str(excinfo.value)


def test_root_swapped_between_validation_and_read_is_refused(tmp_path) -> None:
    from septum_mcp import tools

    _, root, outside = _pinned_root_fixture(tmp_path)
    pinned = tools.load_file_root(str(root))
    error, target = tools._validate_scan_path(
        str(root / "ok.txt"), require_root=True, file_root=pinned
    )
    assert error is None
    # Same inode now lives outside the root, so only the root-descriptor
    # identity check can tell the walk left the pinned directory.
    (root / "ok.txt").replace(outside / "ok.txt")
    root.rename(tmp_path / "root_moved")
    root.symlink_to(outside, target_is_directory=True)

    result = tools._read_scan_target(target)
    assert result.ok is False
    assert result.text == ""


@pytest.mark.parametrize("make", ["missing", "file"])
def test_unusable_root_at_startup_is_a_clear_error(tmp_path, make) -> None:
    from septum_mcp.tools import FileRootError

    root = tmp_path / "root"
    if make == "file":
        root.write_text("not a directory", encoding="utf-8")

    with pytest.raises(FileRootError, match="SEPTUM_MCP_FILE_ROOT"):
        create_server(
            MCPConfig(
                regulations=["gdpr"],
                use_ner_layer=False,
                transport="sse",
                file_root=str(root),
            )
        )


def test_main_exits_with_clear_error_when_root_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SEPTUM_MCP_FILE_ROOT", str(tmp_path / "absent"))
    monkeypatch.delenv("SEPTUM_MCP_TRANSPORT", raising=False)

    with pytest.raises(SystemExit) as excinfo:
        server_module.main(["--transport", "stdio"])
    assert "SEPTUM_MCP_FILE_ROOT" in str(excinfo.value.code)


def test_main_passes_cli_transport_to_server(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def _fake_create_server(cfg):
        captured["transport"] = cfg.transport
        raise SystemExit(0)

    monkeypatch.delenv("SEPTUM_MCP_TRANSPORT", raising=False)
    monkeypatch.setattr(server_module, "create_server", _fake_create_server)
    with pytest.raises(SystemExit):
        server_module.main(["--transport", "streamable-http"])

    assert captured["transport"] == "streamable-http"
