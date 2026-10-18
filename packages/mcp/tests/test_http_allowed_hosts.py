"""Host-header allowlist and serverInfo version tests for the HTTP transports.

The ASGI app is driven in-process through Starlette's ``TestClient``;
nothing binds a socket or leaves the process.
"""

from importlib import metadata

import pytest
from septum_mcp import server as server_module
from septum_mcp.config import AllowedHostsError, MCPConfig, parse_allowed_hosts
from septum_mcp.server import build_http_app, create_server
from starlette.testclient import TestClient

TOKEN = "test-token"
MCP_HEADERS = {
    "authorization": f"Bearer {TOKEN}",
    "accept": "application/json, text/event-stream",
    "content-type": "application/json",
}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test-client", "version": "0"},
    },
}


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch) -> None:
    monkeypatch.setenv("SEPTUM_REGULATIONS", "gdpr")
    monkeypatch.setenv("SEPTUM_USE_NER", "false")
    monkeypatch.delenv("SEPTUM_MCP_HTTP_ALLOWED_HOSTS", raising=False)


def _server():
    return create_server(
        MCPConfig(regulations=["gdpr"], use_ner_layer=False, transport="streamable-http")
    )


def _post_initialize(*, bind_host: str, request_host: str, allowed_hosts=(), origin: str | None = None):
    app = build_http_app(
        _server(),
        transport="streamable-http",
        host=bind_host,
        token=TOKEN,
        mount_path=None,
        allowed_hosts=allowed_hosts,
    )
    headers = {**MCP_HEADERS, "host": request_host}
    if origin is not None:
        headers["origin"] = origin
    with TestClient(app, base_url=f"http://{request_host}") as client:
        return client.post("/mcp", json=INITIALIZE, headers=headers)


def test_server_version_comes_from_package_metadata() -> None:
    server = create_server(MCPConfig(regulations=["gdpr"], use_ner_layer=False))

    assert server.version == metadata.version("septum-mcp")
    assert server.version


def test_initialize_reports_package_version_in_server_info() -> None:
    response = _post_initialize(bind_host="127.0.0.1", request_host="127.0.0.1:8765")

    assert response.status_code == 200
    assert f'"version":"{metadata.version("septum-mcp")}"' in response.text.replace(" ", "")


def test_parse_allowed_hosts_accepts_valid_forms() -> None:
    value = "mcp.internal,mcp.internal:8765,mcp.internal:*,10.0.0.5:8765,[::1]:*,localhost"

    assert parse_allowed_hosts(value) == [
        "mcp.internal",
        "mcp.internal:8765",
        "mcp.internal:*",
        "10.0.0.5:8765",
        "[::1]:*",
        "localhost",
    ]


def test_parse_allowed_hosts_strips_whitespace_and_drops_empties() -> None:
    assert parse_allowed_hosts("  a.example:8765 , ,,b.example  ") == ["a.example:8765", "b.example"]


@pytest.mark.parametrize("value", [None, "", "  ", " , ,"])
def test_parse_allowed_hosts_empty_means_no_allowlist(value) -> None:
    assert parse_allowed_hosts(value) == []


@pytest.mark.parametrize(
    "entry",
    [
        "http://mcp.internal",
        "mcp.internal/mcp",
        "user:secret@mcp.internal",
        "*",
        "mcp.internal:0",
        "mcp.internal:70000",
        "mcp.internal:port",
        "mcp internal",
        "-bad.example",
        "[not-ipv6]:8765",
        "::1",
    ],
)
def test_parse_allowed_hosts_rejects_malformed_entries(entry) -> None:
    with pytest.raises(AllowedHostsError) as excinfo:
        parse_allowed_hosts(f"ok.example,{entry}")

    message = str(excinfo.value)
    assert message.startswith("allowed-hosts entry #2 is invalid")
    assert "secret" not in message


def test_from_env_reads_allowed_hosts() -> None:
    cfg = MCPConfig.from_env({"SEPTUM_MCP_HTTP_ALLOWED_HOSTS": "mcp.internal:*"})

    assert cfg.http_allowed_hosts == ["mcp.internal:*"]
    assert MCPConfig.from_env({}).http_allowed_hosts == []


def test_from_env_rejects_malformed_allowed_hosts() -> None:
    with pytest.raises(AllowedHostsError):
        MCPConfig.from_env({"SEPTUM_MCP_HTTP_ALLOWED_HOSTS": "https://mcp.internal"})


def test_disallowed_host_is_rejected_when_allowlist_set() -> None:
    response = _post_initialize(
        bind_host="0.0.0.0",
        request_host="attacker.example:8765",
        allowed_hosts=["mcp.internal:*"],
    )

    assert response.status_code == 421


def test_allowed_host_passes_when_allowlist_set() -> None:
    response = _post_initialize(
        bind_host="0.0.0.0",
        request_host="mcp.internal:8765",
        allowed_hosts=["mcp.internal:*"],
    )

    assert response.status_code == 200
    assert "serverInfo" in response.text


def test_allowlist_rejects_foreign_origin_and_accepts_listed_one() -> None:
    kwargs = {"bind_host": "0.0.0.0", "request_host": "mcp.internal:8765", "allowed_hosts": ["mcp.internal:*"]}

    assert _post_initialize(**kwargs, origin="https://attacker.example").status_code == 403
    assert _post_initialize(**kwargs, origin="https://mcp.internal:8765").status_code == 200


def test_allowlist_does_not_bypass_bearer_token() -> None:
    app = build_http_app(
        _server(),
        transport="streamable-http",
        host="0.0.0.0",
        token=TOKEN,
        mount_path=None,
        allowed_hosts=["mcp.internal:*"],
    )
    headers = {**MCP_HEADERS, "host": "mcp.internal:8765"}
    del headers["authorization"]
    with TestClient(app, base_url="http://mcp.internal:8765") as client:
        assert client.post("/mcp", json=INITIALIZE, headers=headers).status_code == 401


def test_unset_allowlist_keeps_any_host_on_non_loopback_bind() -> None:
    response = _post_initialize(bind_host="0.0.0.0", request_host="anything.example:8765")

    assert response.status_code == 200


def test_unset_allowlist_keeps_sdk_localhost_check_on_loopback_bind() -> None:
    response = _post_initialize(bind_host="127.0.0.1", request_host="attacker.example:8765")

    assert response.status_code == 421


def test_sse_app_applies_allowlist() -> None:
    app = build_http_app(
        _server(),
        transport="sse",
        host="0.0.0.0",
        token=TOKEN,
        mount_path=None,
        allowed_hosts=["mcp.internal:*"],
    )
    headers = {**MCP_HEADERS, "host": "attacker.example:8765"}
    with TestClient(app, base_url="http://attacker.example:8765") as client:
        response = client.post("/messages/?session_id=00000000000000000000000000000000", json={}, headers=headers)

    assert response.status_code == 421


def _capture_run_http(monkeypatch, argv: list[str]) -> dict:
    captured: dict = {}
    monkeypatch.setattr(server_module, "_run_http", lambda *a, **k: captured.update(k))
    server_module.main(argv)
    return captured


def test_main_passes_env_allowlist_to_http(monkeypatch) -> None:
    monkeypatch.setenv("SEPTUM_MCP_HTTP_ALLOWED_HOSTS", "mcp.internal:*")

    captured = _capture_run_http(monkeypatch, ["--transport", "streamable-http"])

    assert captured["allowed_hosts"] == ["mcp.internal:*"]


def test_main_cli_allowlist_overrides_env(monkeypatch) -> None:
    monkeypatch.setenv("SEPTUM_MCP_HTTP_ALLOWED_HOSTS", "mcp.internal:*")

    captured = _capture_run_http(
        monkeypatch, ["--transport", "streamable-http", "--allowed-hosts", " other.internal:8765 "]
    )

    assert captured["allowed_hosts"] == ["other.internal:8765"]


def test_main_without_allowlist_passes_empty(monkeypatch) -> None:
    captured = _capture_run_http(monkeypatch, ["--transport", "streamable-http"])

    assert captured["allowed_hosts"] == []


@pytest.mark.parametrize("source", ["env", "cli"])
def test_main_exits_with_clear_error_on_malformed_allowlist(monkeypatch, source) -> None:
    argv = ["--transport", "streamable-http"]
    if source == "env":
        monkeypatch.setenv("SEPTUM_MCP_HTTP_ALLOWED_HOSTS", "https://mcp.internal")
    else:
        argv += ["--allowed-hosts", "https://mcp.internal"]
    monkeypatch.setattr(server_module, "_run_http", lambda *a, **k: None)

    with pytest.raises(SystemExit) as excinfo:
        server_module.main(argv)

    assert "allowed-hosts entry #1 is invalid" in str(excinfo.value.code)


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("MCP.Internal", "mcp.internal"),
        ("MCP.Internal:*", "mcp.internal:*"),
        ("[0:0:0:0:0:0:0:1]", "[::1]"),
        ("[0:0:0:0:0:0:0:1]:8765", "[::1]:8765"),
        ("host:08765", "host:8765"),
    ],
)
def test_parse_allowed_hosts_normalises_entries(entry, expected) -> None:
    assert parse_allowed_hosts(entry) == [expected]


def test_mixed_case_entry_matches_lowercase_host_header() -> None:
    response = _post_initialize(
        bind_host="0.0.0.0",
        request_host="mcp.internal:8765",
        allowed_hosts=parse_allowed_hosts("MCP.Internal:*"),
    )

    assert response.status_code == 200


def test_startup_log_reports_allowlist_count_not_entries(monkeypatch, caplog) -> None:
    import sys
    import types

    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=lambda *a, **k: None))
    secret_like = "0123456789abcdef0123456789abcdef"
    with caplog.at_level("INFO", logger="septum_mcp.server"):
        server_module._run_http(
            _server(),
            transport="streamable-http",
            host="0.0.0.0",
            port=8765,
            token=TOKEN,
            mount_path=None,
            allowed_hosts=[secret_like, "mcp.internal:*"],
        )

    assert secret_like not in caplog.text
    assert "allowed_hosts=2 entries" in caplog.text
