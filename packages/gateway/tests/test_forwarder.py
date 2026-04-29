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
        forwarder = AnthropicForwarder(api_key="sk-anth")
        result = await forwarder.complete(_env("anthropic"))
        assert result == "Hello, [PERSON_1]."
        await forwarder.aclose()

    @respx.mock
    async def test_sends_configured_api_key_in_x_api_key_header(self):
        route = respx.post("https://api.anthropic.com/v1/messages").respond(
            200, json={"content": [{"type": "text", "text": "ok"}]}
        )
        forwarder = AnthropicForwarder(api_key="sk-env")
        await forwarder.complete(_env("anthropic"))
        sent_headers = route.calls.last.request.headers
        # Gateway holds the only credential — no per-envelope override.
        assert sent_headers["x-api-key"] == "sk-env"
        assert sent_headers["anthropic-version"] == "2023-06-01"
        await forwarder.aclose()

    @respx.mock
    async def test_idempotency_key_uses_correlation_id(self):
        route = respx.post("https://api.anthropic.com/v1/messages").respond(
            200, json={"content": [{"type": "text", "text": "ok"}]}
        )
        forwarder = AnthropicForwarder(api_key="sk")
        envelope = _env("anthropic")
        await forwarder.complete(envelope)
        assert (
            route.calls.last.request.headers["Idempotency-Key"]
            == envelope.correlation_id
        )
        await forwarder.aclose()

    async def test_missing_api_key_raises_gateway_error(self):
        forwarder = AnthropicForwarder(api_key=None)
        with pytest.raises(GatewayError, match="API key is not configured"):
            await forwarder.complete(_env("anthropic"))
        await forwarder.aclose()

    async def test_disallowed_base_url_host_is_rejected(self):
        forwarder = AnthropicForwarder(api_key="sk")
        with pytest.raises(GatewayError, match="not on the allow-list"):
            await forwarder.complete(
                _env("anthropic", base_url="https://evil.example.com/v1/messages")
            )
        await forwarder.aclose()

    async def test_http_scheme_base_url_is_rejected(self):
        forwarder = AnthropicForwarder(api_key="sk")
        with pytest.raises(GatewayError, match="https"):
            await forwarder.complete(
                _env("anthropic", base_url="http://api.anthropic.com/v1/messages")
            )
        await forwarder.aclose()


class TestOpenAIForwarder:
    @respx.mock
    async def test_happy_path_extracts_choice_content(self):
        respx.post("https://api.openai.com/v1/chat/completions").respond(
            200,
            json={"choices": [{"message": {"content": "[PERSON_1] is in [CITY_1]."}}]},
        )
        forwarder = OpenAIForwarder(api_key="sk-openai")
        result = await forwarder.complete(_env("openai"))
        assert result == "[PERSON_1] is in [CITY_1]."
        await forwarder.aclose()

    @respx.mock
    async def test_empty_choices_raises_gateway_error(self):
        respx.post("https://api.openai.com/v1/chat/completions").respond(
            200, json={"choices": []}
        )
        forwarder = OpenAIForwarder(api_key="sk")
        with pytest.raises(GatewayError, match="did not contain any choices"):
            await forwarder.complete(_env("openai"))
        await forwarder.aclose()

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
        forwarder = OpenAIForwarder(
            api_key="sk", max_attempts=2, base_backoff_seconds=0.01
        )
        result = await forwarder.complete(_env("openai"))
        assert result == "recovered"
        assert route.call_count == 2
        await forwarder.aclose()

    @respx.mock
    async def test_4xx_does_not_retry(self):
        """Non-retryable status fails fast — no second attempt."""
        route = respx.post("https://api.openai.com/v1/chat/completions").respond(
            400, json={"error": "bad request"}
        )
        forwarder = OpenAIForwarder(
            api_key="sk", max_attempts=3, base_backoff_seconds=0.01
        )
        with pytest.raises(GatewayError, match="status=400"):
            await forwarder.complete(_env("openai"))
        assert route.call_count == 1
        await forwarder.aclose()

    @respx.mock
    async def test_429_retries_with_retry_after_honored(self):
        route = respx.post("https://api.openai.com/v1/chat/completions").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "0"}),
                httpx.Response(
                    200, json={"choices": [{"message": {"content": "ok"}}]}
                ),
            ]
        )
        forwarder = OpenAIForwarder(
            api_key="sk", max_attempts=2, base_backoff_seconds=10.0
        )
        result = await forwarder.complete(_env("openai"))
        assert result == "ok"
        assert route.call_count == 2
        await forwarder.aclose()


class TestOpenRouterForwarder:
    @respx.mock
    async def test_adds_openrouter_branding_headers(self):
        route = respx.post("https://openrouter.ai/api/v1/chat/completions").respond(
            200, json={"choices": [{"message": {"content": "ok"}}]}
        )
        forwarder = OpenRouterForwarder(api_key="sk-or")
        await forwarder.complete(_env("openrouter"))
        headers = route.calls.last.request.headers
        assert headers["HTTP-Referer"] == "https://septum.local"
        assert headers["X-Title"] == "Septum"
        await forwarder.aclose()


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
