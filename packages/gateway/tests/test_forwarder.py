"""Forwarder tests using respx to intercept httpx traffic.

These exercise the provider-specific request shape (URL, headers,
body) and the response-parsing logic without hitting the real cloud
APIs. The retry + timeout logic is covered by one deliberate transient
failure test; the rest is mocked at the first attempt.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from septum_gateway import (
    AnthropicForwarder,
    ForwarderRegistry,
    GatewayError,
    OpenAIForwarder,
    OpenRouterForwarder,
)
from septum_queue import RequestEnvelope


def _env(provider: str, **overrides) -> RequestEnvelope:
    defaults = {
        "provider": provider,
        "model": "test-model",
        "messages": [{"role": "user", "content": "hello"}],
    }
    defaults.update(overrides)
    return RequestEnvelope.new(**defaults)


class TestAnthropicForwarder:
    @respx.mock
    async def test_happy_path_extracts_text_blocks(self):
        respx.post("https://api.anthropic.com/v1/messages").respond(
            200,
            json={
                "content": [
                    {"type": "text", "text": "Hello, "},
                    {"type": "text", "text": "[PERSON_1]."},
                ]
            },
        )
        forwarder = AnthropicForwarder(default_api_key="sk-anth")
        result = await forwarder.complete(_env("anthropic"))
        assert result == "Hello, [PERSON_1]."

    @respx.mock
    async def test_sends_api_key_in_x_api_key_header(self):
        route = respx.post("https://api.anthropic.com/v1/messages").respond(
            200, json={"content": [{"type": "text", "text": "ok"}]}
        )
        forwarder = AnthropicForwarder(default_api_key="sk-env")
        await forwarder.complete(_env("anthropic", api_key="sk-envelope"))
        sent_headers = route.calls.last.request.headers
        # Envelope-supplied key must win over the configured default.
        assert sent_headers["x-api-key"] == "sk-envelope"
        assert sent_headers["anthropic-version"] == "2023-06-01"

    async def test_missing_api_key_raises_gateway_error(self):
        forwarder = AnthropicForwarder(default_api_key=None)
        with pytest.raises(GatewayError, match="Anthropic API key is not configured"):
            await forwarder.complete(_env("anthropic"))

    @respx.mock
    async def test_base_url_override_in_envelope_wins(self):
        custom = "https://proxy.example.com/v1/messages"
        respx.post(custom).respond(
            200, json={"content": [{"type": "text", "text": "ok"}]}
        )
        forwarder = AnthropicForwarder(default_api_key="sk")
        result = await forwarder.complete(_env("anthropic", base_url=custom))
        assert result == "ok"


class TestOpenAIForwarder:
    @respx.mock
    async def test_happy_path_extracts_choice_content(self):
        respx.post("https://api.openai.com/v1/chat/completions").respond(
            200,
            json={"choices": [{"message": {"content": "[PERSON_1] is in [CITY_1]."}}]},
        )
        forwarder = OpenAIForwarder(default_api_key="sk-openai")
        result = await forwarder.complete(_env("openai"))
        assert result == "[PERSON_1] is in [CITY_1]."

    @respx.mock
    async def test_empty_choices_raises_gateway_error(self):
        respx.post("https://api.openai.com/v1/chat/completions").respond(
            200, json={"choices": []}
        )
        forwarder = OpenAIForwarder(default_api_key="sk")
        with pytest.raises(GatewayError, match="did not contain any choices"):
            await forwarder.complete(_env("openai"))

    @respx.mock
    async def test_retries_on_5xx_then_succeeds(self):
        route = respx.post("https://api.openai.com/v1/chat/completions").mock(
            side_effect=[
                httpx.Response(502, json={"error": "bad gateway"}),
                httpx.Response(
                    200, json={"choices": [{"message": {"content": "recovered"}}]}
                ),
            ]
        )
        forwarder = OpenAIForwarder(default_api_key="sk", max_attempts=2)
        result = await forwarder.complete(_env("openai"))
        assert result == "recovered"
        assert route.call_count == 2


class TestOpenRouterForwarder:
    @respx.mock
    async def test_adds_openrouter_branding_headers(self):
        route = respx.post("https://openrouter.ai/api/v1/chat/completions").respond(
            200, json={"choices": [{"message": {"content": "ok"}}]}
        )
        forwarder = OpenRouterForwarder(default_api_key="sk-or")
        await forwarder.complete(_env("openrouter"))
        headers = route.calls.last.request.headers
        assert headers["HTTP-Referer"] == "https://septum.local"
        assert headers["X-Title"] == "Septum"


class TestForwarderRegistry:
    def test_from_config_registers_all_three_cloud_providers(self):
        from septum_gateway import GatewayConfig

        registry = ForwarderRegistry.from_config(
            GatewayConfig(
                anthropic_api_key="a",
                openai_api_key="o",
                openrouter_api_key="r",
            )
        )
        assert isinstance(registry.for_provider("anthropic"), AnthropicForwarder)
        assert isinstance(registry.for_provider("openai"), OpenAIForwarder)
        assert isinstance(registry.for_provider("openrouter"), OpenRouterForwarder)

    def test_unknown_provider_raises(self):
        registry = ForwarderRegistry()
        with pytest.raises(GatewayError, match="unsupported provider"):
            registry.for_provider("cohere")

    def test_register_substitutes_fake_forwarder(self):
        class Fake:
            async def complete(self, envelope):
                return "fake"

        registry = ForwarderRegistry()
        registry.register("anthropic", Fake())
        assert registry.for_provider("ANTHROPIC") is not None
