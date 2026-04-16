"""GatewayConfig env loading and provider key resolution tests."""

from __future__ import annotations

import pytest

from septum_gateway import GatewayConfig


class TestGatewayConfig:
    def test_defaults_use_septum_prefix_topics(self):
        config = GatewayConfig()
        assert config.request_topic == "septum.llm.requests"
        assert config.response_topic == "septum.llm.responses"
        assert config.request_timeout_seconds == 30.0
        assert config.max_attempts == 3

    def test_from_env_reads_septum_prefixed_variables(self, monkeypatch):
        monkeypatch.setenv("SEPTUM_GATEWAY_ANTHROPIC_API_KEY", "sk-anth")
        monkeypatch.setenv("SEPTUM_GATEWAY_TIMEOUT_SECONDS", "45")
        monkeypatch.setenv("SEPTUM_GATEWAY_MAX_ATTEMPTS", "5")
        config = GatewayConfig.from_env()
        assert config.anthropic_api_key == "sk-anth"
        assert config.request_timeout_seconds == 45.0
        assert config.max_attempts == 5

    def test_from_env_falls_back_to_legacy_anthropic_api_key(self, monkeypatch):
        monkeypatch.delenv("SEPTUM_GATEWAY_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-legacy")
        config = GatewayConfig.from_env()
        assert config.anthropic_api_key == "sk-legacy"

    def test_septum_prefix_wins_over_legacy_name(self, monkeypatch):
        monkeypatch.setenv("SEPTUM_GATEWAY_OPENAI_API_KEY", "sk-new")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-old")
        config = GatewayConfig.from_env()
        assert config.openai_api_key == "sk-new"

    @pytest.mark.parametrize(
        "provider,attribute",
        [
            ("anthropic", "anthropic_api_key"),
            ("OpenAI", "openai_api_key"),
            ("OPENROUTER", "openrouter_api_key"),
        ],
    )
    def test_api_key_for_dispatches_case_insensitively(self, provider, attribute):
        config = GatewayConfig(**{attribute: "sk-test"})
        assert config.api_key_for(provider) == "sk-test"

    def test_api_key_for_unknown_provider_returns_none(self):
        config = GatewayConfig(anthropic_api_key="sk-a")
        assert config.api_key_for("cohere") is None
