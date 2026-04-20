from __future__ import annotations

"""Tests for :class:`septum_mcp.config.MCPConfig`."""

from septum_mcp.config import DEFAULT_REGULATIONS, MCPConfig


def test_from_env_applies_defaults_when_env_is_empty() -> None:
    cfg = MCPConfig.from_env({})

    assert cfg.regulations == list(DEFAULT_REGULATIONS)
    assert cfg.language == "en"
    assert cfg.use_ner_layer is True
    assert cfg.use_presidio_layer is True
    assert cfg.session_ttl_seconds == 3600.0


def test_from_env_parses_comma_separated_regulations() -> None:
    cfg = MCPConfig.from_env({"SEPTUM_REGULATIONS": "gdpr, KVKK,hipaa"})

    assert cfg.regulations == ["gdpr", "kvkk", "hipaa"]


def test_from_env_parses_boolean_flags() -> None:
    cfg = MCPConfig.from_env(
        {
            "SEPTUM_USE_NER": "false",
            "SEPTUM_USE_PRESIDIO": "0",
        }
    )

    assert cfg.use_ner_layer is False
    assert cfg.use_presidio_layer is False


def test_from_env_parses_ttl() -> None:
    cfg = MCPConfig.from_env({"SEPTUM_SESSION_TTL": "120"})

    assert cfg.session_ttl_seconds == 120.0


def test_from_env_falls_back_to_default_when_ttl_is_garbage() -> None:
    cfg = MCPConfig.from_env({"SEPTUM_SESSION_TTL": "not-a-number"})

    assert cfg.session_ttl_seconds == 3600.0


def test_from_env_strips_empty_regulation_entries() -> None:
    cfg = MCPConfig.from_env({"SEPTUM_REGULATIONS": "gdpr,,  , kvkk"})

    assert cfg.regulations == ["gdpr", "kvkk"]


def test_from_env_http_defaults() -> None:
    cfg = MCPConfig.from_env({})

    assert cfg.transport == "stdio"
    assert cfg.http_host == "127.0.0.1"
    assert cfg.http_port == 8765
    assert cfg.http_token is None
    assert cfg.http_mount_path is None


def test_from_env_parses_http_transport_variables() -> None:
    cfg = MCPConfig.from_env(
        {
            "SEPTUM_MCP_TRANSPORT": "streamable-http",
            "SEPTUM_MCP_HTTP_HOST": "0.0.0.0",
            "SEPTUM_MCP_HTTP_PORT": "9000",
            "SEPTUM_MCP_HTTP_TOKEN": "  super-secret ",
            "SEPTUM_MCP_HTTP_MOUNT_PATH": "/api/mcp",
        }
    )

    assert cfg.transport == "streamable-http"
    assert cfg.http_host == "0.0.0.0"
    assert cfg.http_port == 9000
    assert cfg.http_token == "super-secret"
    assert cfg.http_mount_path == "/api/mcp"


def test_from_env_unknown_transport_falls_back_to_stdio() -> None:
    cfg = MCPConfig.from_env({"SEPTUM_MCP_TRANSPORT": "websocket"})

    assert cfg.transport == "stdio"


def test_from_env_invalid_port_falls_back_to_default() -> None:
    cfg = MCPConfig.from_env({"SEPTUM_MCP_HTTP_PORT": "not-a-number"})

    assert cfg.http_port == 8765


def test_from_env_out_of_range_port_falls_back_to_default() -> None:
    cfg = MCPConfig.from_env({"SEPTUM_MCP_HTTP_PORT": "99999"})

    assert cfg.http_port == 8765


def test_from_env_blank_token_normalises_to_none() -> None:
    cfg = MCPConfig.from_env({"SEPTUM_MCP_HTTP_TOKEN": "   "})

    assert cfg.http_token is None
