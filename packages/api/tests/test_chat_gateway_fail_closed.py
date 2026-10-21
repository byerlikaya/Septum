"""Gateway routing fails closed end to end, and the api/gateway stream keys agree.

Every LLM, queue and provider call is mocked or faked; ``respx`` proves that
no outbound HTTP request is attempted.
"""

from __future__ import annotations

from typing import Any

import pytest
import respx
from chat_harness import answer_text
from httpx import AsyncClient
from sqlalchemy import update

from septum_api.database import build_default_app_settings, get_db
from septum_api.main import app
from septum_api.models.settings import AppSettings
from septum_api.services import llm_router, ollama_client
from septum_api.services.gateway_client import GatewayClient
from septum_api.services.llm_providers import health
from septum_api.services.llm_router import GATEWAY_NOT_CONFIGURED_MESSAGE, LLMRouter

# Captured at import, before the chat fixture replaces ``complete`` with a stub.
_REAL_COMPLETE = LLMRouter.complete
CODE = "gateway_not_configured"


@pytest.fixture(autouse=True)
def _no_factory():
    health.reset("openai:gpt-4o")
    llm_router.set_gateway_client_factory(None)
    yield
    llm_router.set_gateway_client_factory(None)
    health.reset("openai:gpt-4o")


async def _configure(chat_env, **overrides: Any) -> None:
    async with chat_env.session_maker() as session:
        settings = build_default_app_settings()
        settings.require_approval = False
        settings.use_gateway = True
        settings.llm_provider = "openai"
        settings.llm_model = "gpt-4o"
        settings.openai_api_key = "sk-test"
        for key, value in overrides.items():
            setattr(settings, key, value)
        session.add(settings)
        await session.commit()


async def _ask(chat_env, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(LLMRouter, "complete", _REAL_COMPLETE)
    doc = await chat_env.add_document(chat_env.users["alice"], "a.pdf", ["alpha"])
    chat_env.act_as("alice")
    _, events, _ = await chat_env.ask({"message": "who?", "document_ids": [doc]})
    return events


class TestChatFailsClosed:
    async def test_no_factory_yields_coded_error_and_no_outbound_request(
        self, chat_env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _configure(chat_env)

        with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
            events = await _ask(chat_env, monkeypatch)

        errors = [e for e in events if e["type"] == "error"]
        assert len(errors) == 1
        assert errors[0]["code"] == CODE
        assert errors[0]["message"] == GATEWAY_NOT_CONFIGURED_MESSAGE
        assert answer_text(events) == ""
        assert len(router.calls) == 0

    async def test_env_forced_gateway_overrides_stored_off_with_no_outbound_request(
        self, chat_env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _configure(chat_env, use_gateway=False)
        monkeypatch.setenv("USE_GATEWAY_DEFAULT", "true")

        with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
            events = await _ask(chat_env, monkeypatch)

        errors = [e for e in events if e["type"] == "error"]
        assert len(errors) == 1
        assert errors[0]["code"] == CODE
        assert answer_text(events) == ""
        assert len(router.calls) == 0

    async def test_no_factory_does_not_fall_back_to_local_ollama(
        self, chat_env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _configure(chat_env)
        local_calls: list[str] = []

        async def _local(**kwargs: Any) -> str:
            local_calls.append(kwargs["base_url"])
            return "local answer"

        monkeypatch.setattr(ollama_client, "call_ollama_async", _local)

        events = await _ask(chat_env, monkeypatch)

        assert [e["code"] for e in events if e["type"] == "error"] == [CODE]
        assert local_calls == []

    async def test_ollama_provider_stays_direct_with_gateway_on(
        self, chat_env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _configure(chat_env, llm_provider="ollama", llm_model="llama3.2:3b")
        local_calls: list[str] = []
        factory_calls: list[AppSettings] = []

        async def _local(**kwargs: Any) -> str:
            local_calls.append(kwargs["base_url"])
            return "local answer"

        def _factory(settings: AppSettings):
            factory_calls.append(settings)
            raise AssertionError("ollama must never reach the gateway")

        monkeypatch.setattr(ollama_client, "call_ollama_async", _local)
        llm_router.set_gateway_client_factory(_factory)

        with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
            events = await _ask(chat_env, monkeypatch)

        assert [e for e in events if e["type"] == "error"] == []
        assert "local answer" in answer_text(events)
        assert len(local_calls) == 1
        assert factory_calls == []
        assert len(router.calls) == 0


class TestSettingsTestLlmFailsClosed:
    async def test_test_llm_returns_400_with_the_code_and_no_request(
        self, router_client: AsyncClient
    ) -> None:
        from test_settings_ollama_url import _admin_headers

        headers = await _admin_headers(router_client)
        patched = await router_client.patch(
            "/api/settings",
            headers=headers,
            json={"llm_provider": "openai", "llm_model": "gpt-4o"},
        )
        assert patched.status_code == 200, patched.text
        session_gen = app.dependency_overrides[get_db]()
        session = await session_gen.__anext__()
        try:
            await session.execute(update(AppSettings).values(use_gateway=True))
            await session.commit()
        finally:
            await session_gen.aclose()

        with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
            resp = await router_client.post(
                "/api/settings/test-llm",
                headers=headers,
                json={"provider": "openai", "model": "gpt-4o"},
            )

        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["code"] == CODE
        assert body["detail"] == GATEWAY_NOT_CONFIGURED_MESSAGE
        assert len(router.calls) == 0


class TestSettingsTestLlmForcedByEnv:
    async def test_env_forced_gateway_routes_test_llm_through_the_client(
        self, router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from test_settings_ollama_url import _admin_headers

        headers = await _admin_headers(router_client)
        patched = await router_client.patch(
            "/api/settings",
            headers=headers,
            json={"llm_provider": "openai", "llm_model": "gpt-4o"},
        )
        assert patched.status_code == 200, patched.text
        calls: list[AppSettings] = []

        class _Gateway:
            async def complete(self, *, settings: AppSettings, **_: Any) -> str:
                calls.append(settings)
                return "[PERSON_1] ok"

        monkeypatch.setenv("USE_GATEWAY_DEFAULT", "true")
        llm_router.set_gateway_client_factory(lambda _settings: _Gateway())

        with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
            resp = await router_client.post(
                "/api/settings/test-llm",
                headers=headers,
                json={"provider": "openai", "model": "gpt-4o"},
            )

        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True
        assert len(calls) == 1 and not calls[0].use_gateway
        assert len(router.calls) == 0


class TestStreamKeysMatchBetweenApiAndGateway:
    async def test_client_publishes_and_consumes_the_gateway_default_streams(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fakeredis = pytest.importorskip("fakeredis")
        from septum_gateway.config import GatewayConfig
        from septum_queue import (
            LLM_REQUEST_TOPIC,
            LLM_RESPONSE_TOPIC,
            RedisStreamsQueueBackend,
            RequestEnvelope,
            ResponseEnvelope,
        )

        config = GatewayConfig()
        assert config.request_topic == LLM_REQUEST_TOPIC
        assert config.response_topic == LLM_RESPONSE_TOPIC

        server = fakeredis.FakeServer()

        def _backend(topic: str) -> RedisStreamsQueueBackend:
            return RedisStreamsQueueBackend(
                fakeredis.aioredis.FakeRedis(server=server), topic=topic
            )

        client_requests = _backend(LLM_REQUEST_TOPIC)
        client_responses = _backend(LLM_RESPONSE_TOPIC)
        gateway_requests = _backend(config.request_topic)
        gateway_responses = _backend(config.response_topic)
        client = GatewayClient(
            request_queue=client_requests, response_queue=client_responses, timeout_seconds=5
        )
        publish = client_requests.publish

        async def _publish_then_serve(payload: Any) -> str:
            entry_id = await publish(payload)
            async for message in gateway_requests.consume(batch_size=1, block_ms=0):
                request = RequestEnvelope.from_dict(message.payload)
                await gateway_responses.publish(
                    ResponseEnvelope(
                        correlation_id=request.correlation_id, text="[PERSON_1] ok"
                    ).to_dict()
                )
                await gateway_requests.ack(message.id)
            return entry_id

        monkeypatch.setattr(client_requests, "publish", _publish_then_serve)
        settings = build_default_app_settings()
        settings.llm_provider = "openai"
        settings.llm_model = "gpt-4o"
        settings.openai_api_key = "sk-test"

        text = await client.complete(
            settings=settings, messages=[{"role": "user", "content": "hi"}]
        )

        redis = fakeredis.aioredis.FakeRedis(server=server)
        keys = {k.decode() if isinstance(k, bytes) else k for k in await redis.keys("*")}
        assert text == "[PERSON_1] ok"
        assert f"septum:{config.request_topic}" in keys
        assert f"septum:{config.response_topic}" in keys
        assert await redis.xlen(f"septum:{config.request_topic}") == 0
        assert await redis.xlen(f"septum:{config.response_topic}") == 0
        for backend in (client_requests, client_responses, gateway_requests, gateway_responses):
            await backend.close()
        await redis.aclose()

    def test_gateway_env_defaults_resolve_to_the_shared_constants(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from septum_gateway.config import GatewayConfig
        from septum_queue import LLM_REQUEST_TOPIC, LLM_RESPONSE_TOPIC

        for name in ("SEPTUM_GATEWAY_REQUEST_TOPIC", "SEPTUM_GATEWAY_RESPONSE_TOPIC"):
            monkeypatch.delenv(name, raising=False)

        config = GatewayConfig.from_env()

        assert (config.request_topic, config.response_topic) == (
            LLM_REQUEST_TOPIC,
            LLM_RESPONSE_TOPIC,
        )
