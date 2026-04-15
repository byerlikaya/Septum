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
