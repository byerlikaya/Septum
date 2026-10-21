"""The API lifespan wires one shared gateway client from the queue environment."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
from factories.app_settings_factory import make_app_settings
from septum_api import main
from septum_api.env_flags import EnvFlagError, env_bool
from septum_api.services import gateway_client as gateway_client_module
from septum_api.services import llm_router
from septum_api.services.llm_errors import GatewayStartupError
from septum_api.services.llm_providers import health
from septum_api.services.llm_router import LLMRouter
from septum_queue import (
    LLM_REQUEST_TOPIC,
    LLM_RESPONSE_TOPIC,
    RedisStreamsQueueBackend,
    RequestEnvelope,
    ResponseEnvelope,
)

SECRET = "s3cret-queue-pass"
QUEUE_URL = f"redis://septum-api:{SECRET}@queue.invalid:6379/0"
ENV_VARS = (
    "SEPTUM_QUEUE_URL",
    "SEPTUM_QUEUE_DIR",
    "USE_GATEWAY_DEFAULT",
    "SEPTUM_GATEWAY_REQUEST_TOPIC",
    "SEPTUM_GATEWAY_RESPONSE_TOPIC",
    "SEPTUM_GATEWAY_CLIENT_TIMEOUT_SECONDS",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(main.bootstrap, "needs_setup", lambda: True)
    llm_router.set_gateway_client_factory(None)
    yield
    llm_router.set_gateway_client_factory(None)


@pytest.fixture
def backend_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every ``backend_from_env`` call while keeping the real behaviour."""
    calls: list[str] = []
    real = gateway_client_module.backend_from_env

    def _recording(topic: str):
        calls.append(topic)
        return real(topic)

    monkeypatch.setattr(gateway_client_module, "backend_from_env", _recording)
    return calls


@pytest.fixture
def fake_redis_server(monkeypatch: pytest.MonkeyPatch):
    """Route ``RedisStreamsQueueBackend.from_url`` to one shared in-memory server."""
    fakeredis = pytest.importorskip("fakeredis")
    server = fakeredis.FakeServer()

    def _fake_from_url(cls, url, *, topic, **kwargs):
        return cls(fakeredis.aioredis.FakeRedis(server=server), topic=topic)

    monkeypatch.setattr(RedisStreamsQueueBackend, "from_url", classmethod(_fake_from_url))
    return server


def _installed_client() -> Any:
    factory = llm_router._gateway_client_factory
    assert factory is not None
    return factory(make_app_settings(provider="openai", model="m"))


async def test_unset_queue_env_builds_nothing(backend_calls: list[str]) -> None:
    async with main.lifespan(main.app):
        assert llm_router._gateway_client_factory is None

    assert backend_calls == []


async def test_gateway_default_without_queue_refuses_startup(
    monkeypatch: pytest.MonkeyPatch, backend_calls: list[str]
) -> None:
    engine_inits: list[str] = []
    monkeypatch.setenv("USE_GATEWAY_DEFAULT", "true")
    monkeypatch.setattr(main.bootstrap, "needs_setup", lambda: False)
    monkeypatch.setattr(main, "initialize_engine", engine_inits.append)

    with pytest.raises(GatewayStartupError, match="USE_GATEWAY_DEFAULT"):
        async with main.lifespan(main.app):
            pass

    assert backend_calls == []
    assert engine_inits == []
    assert llm_router._gateway_client_factory is None


@pytest.mark.parametrize("queue_dir", [False, True])
async def test_unrecognised_gateway_default_refuses_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, backend_calls: list[str], queue_dir: bool
) -> None:
    monkeypatch.setenv("USE_GATEWAY_DEFAULT", "ture")
    if queue_dir:
        monkeypatch.setenv("SEPTUM_QUEUE_DIR", str(tmp_path))

    with pytest.raises(EnvFlagError, match="USE_GATEWAY_DEFAULT") as exc_info:
        async with main.lifespan(main.app):
            pass

    assert "ture" not in str(exc_info.value)
    assert backend_calls == []
    assert llm_router._gateway_client_factory is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True), ("TRUE", True), (" 1 ", True), ("yes", True), ("on", True),
        ("false", False), ("False", False), ("0", False), ("no", False), ("off", False),
        ("", False), ("   ", False),
    ],
)
def test_env_bool_accepts_documented_spellings(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    monkeypatch.setenv("USE_GATEWAY_DEFAULT", raw)

    assert env_bool("USE_GATEWAY_DEFAULT", False) is expected


@pytest.mark.parametrize("raw", ["", "  ", None])
def test_env_bool_blank_or_unset_keeps_a_true_default(
    monkeypatch: pytest.MonkeyPatch, raw: str | None
) -> None:
    if raw is None:
        monkeypatch.delenv("USE_GATEWAY_DEFAULT", raising=False)
    else:
        monkeypatch.setenv("USE_GATEWAY_DEFAULT", raw)

    assert env_bool("USE_GATEWAY_DEFAULT", True) is True
    assert env_bool("USE_GATEWAY_DEFAULT", False) is False


@pytest.mark.parametrize("raw", ["ture", "enabled", "2", "y", "true-ish"])
def test_env_bool_refuses_other_values(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("USE_GATEWAY_DEFAULT", raw)

    with pytest.raises(EnvFlagError, match="USE_GATEWAY_DEFAULT"):
        env_bool("USE_GATEWAY_DEFAULT", True)


async def test_queue_dir_installs_one_shared_client_and_closes_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, backend_calls: list[str]
) -> None:
    monkeypatch.setenv("SEPTUM_QUEUE_DIR", str(tmp_path))

    async with main.lifespan(main.app):
        client = _installed_client()
        assert _installed_client() is client
        assert client._request_queue.topic == LLM_REQUEST_TOPIC
        assert client._response_queue.topic == LLM_RESPONSE_TOPIC

    assert backend_calls == [LLM_REQUEST_TOPIC, LLM_RESPONSE_TOPIC]
    assert llm_router._gateway_client_factory is None
    assert client._request_queue._closed and client._response_queue._closed


async def test_redis_queue_round_trip_through_router(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis_server: Any,
    backend_calls: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Topic overrides reach the backends and a chat call is answered over the queue."""
    import fakeredis

    monkeypatch.setenv("SEPTUM_QUEUE_URL", QUEUE_URL)
    monkeypatch.setenv("SEPTUM_GATEWAY_REQUEST_TOPIC", "custom.requests")
    monkeypatch.setenv("SEPTUM_GATEWAY_RESPONSE_TOPIC", "custom.responses")
    settings = make_app_settings(provider="openai", model="wiring-model")
    settings.openai_api_key = "sk-test"
    settings.use_gateway = True
    health.reset("openai:wiring-model")

    gateway_requests = RedisStreamsQueueBackend(
        fakeredis.aioredis.FakeRedis(server=fake_redis_server), topic="custom.requests"
    )
    gateway_responses = RedisStreamsQueueBackend(
        fakeredis.aioredis.FakeRedis(server=fake_redis_server), topic="custom.responses"
    )

    async def _serve_one_request() -> None:
        async for message in gateway_requests.consume(batch_size=1, block_ms=0):
            request = RequestEnvelope.from_dict(message.payload)
            await gateway_responses.publish(
                ResponseEnvelope(
                    correlation_id=request.correlation_id, text="[PERSON_1] answered."
                ).to_dict()
            )
            await gateway_requests.ack(message.id)

    try:
        with caplog.at_level(logging.DEBUG):
            async with main.lifespan(main.app):
                client = _installed_client()
                assert client._request_queue.topic == "custom.requests"
                assert client._response_queue.topic == "custom.responses"
                publish = client._request_queue.publish

                async def _publish_and_serve(payload: Any) -> str:
                    entry_id = await publish(payload)
                    await _serve_one_request()
                    return entry_id

                # fakeredis answers a blocking XREADGROUP at once, so the
                # gateway side runs inline after the publish instead of as a task.
                monkeypatch.setattr(client._request_queue, "publish", _publish_and_serve)
                text = "".join(
                    [
                        chunk
                        async for chunk in LLMRouter(settings).stream_chat(
                            messages=[{"role": "user", "content": "hi"}]
                        )
                    ]
                )
    finally:
        await gateway_requests.close()
        await gateway_responses.close()
        health.reset("openai:wiring-model")
    assert text == "[PERSON_1] answered."
    assert backend_calls == ["custom.requests", "custom.responses"]
    assert llm_router._gateway_client_factory is None
    assert client._request_queue._closed and client._response_queue._closed
    assert SECRET not in caplog.text


@pytest.mark.parametrize(
    "url",
    [f"http://septum-api:{SECRET}@queue.invalid:6379/0", f"redis://:{SECRET}@queue.invalid:port/0"],
    ids=["bad-scheme", "bad-port"],
)
async def test_unusable_queue_url_refuses_startup_without_secret(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, url: str
) -> None:
    monkeypatch.setenv("SEPTUM_QUEUE_URL", url)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(GatewayStartupError) as exc:
            async with main.lifespan(main.app):
                pass

    assert "SEPTUM_QUEUE_URL" in str(exc.value)
    assert SECRET not in str(exc.value)
    assert "queue.invalid" not in str(exc.value)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__
    assert SECRET not in caplog.text
    assert llm_router._gateway_client_factory is None


async def test_invalid_client_timeout_refuses_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, backend_calls: list[str]
) -> None:
    monkeypatch.setenv("SEPTUM_QUEUE_DIR", str(tmp_path))
    monkeypatch.setenv("SEPTUM_GATEWAY_CLIENT_TIMEOUT_SECONDS", "soon")

    with pytest.raises(GatewayStartupError, match="SEPTUM_GATEWAY_CLIENT_TIMEOUT_SECONDS"):
        async with main.lifespan(main.app):
            pass

    assert backend_calls == []


async def test_valid_queue_with_gateway_default_true_starts_normally(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, backend_calls: list[str]
) -> None:
    monkeypatch.setenv("USE_GATEWAY_DEFAULT", "true")
    monkeypatch.setenv("SEPTUM_QUEUE_DIR", str(tmp_path))

    async with main.lifespan(main.app):
        client = _installed_client()
        assert client is _installed_client()

    assert backend_calls == [LLM_REQUEST_TOPIC, LLM_RESPONSE_TOPIC]
    assert llm_router._gateway_client_factory is None
    assert client._request_queue._closed and client._response_queue._closed


async def test_env_forced_gateway_routes_a_router_call_through_the_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("USE_GATEWAY_DEFAULT", "true")
    monkeypatch.setenv("SEPTUM_QUEUE_DIR", str(tmp_path))
    settings = make_app_settings(provider="openai", model="forced-model")
    settings.openai_api_key = "sk-test"
    settings.use_gateway = False
    health.reset("openai:forced-model")
    seen: list[Any] = []

    try:
        async with main.lifespan(main.app):
            client = _installed_client()

            async def _complete(**kwargs: Any) -> str:
                seen.append(kwargs["settings"])
                return "[PERSON_1] forced"

            monkeypatch.setattr(client, "complete", _complete)
            text = "".join(
                [
                    chunk
                    async for chunk in LLMRouter(settings).stream_chat(
                        messages=[{"role": "user", "content": "hi"}]
                    )
                ]
            )
    finally:
        health.reset("openai:forced-model")

    assert text == "[PERSON_1] forced"
    assert len(seen) == 1


async def test_later_startup_failure_closes_and_clears_the_gateway_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SEPTUM_QUEUE_DIR", str(tmp_path))
    captured: list[Any] = []

    async def _failing_step() -> None:
        captured.append(_installed_client())
        raise RuntimeError("later startup step failed")

    monkeypatch.setattr(main, "_announce_setup_token", _failing_step)

    with pytest.raises(RuntimeError, match="later startup step failed"):
        async with main.lifespan(main.app):
            pytest.fail("lifespan must not yield")

    client = captured[0]
    assert llm_router._gateway_client_factory is None
    assert client._request_queue._closed and client._response_queue._closed


async def test_aclose_failure_on_shutdown_still_clears_the_factory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SEPTUM_QUEUE_DIR", str(tmp_path))
    closed: list[bool] = []

    with pytest.raises(RuntimeError, match="close failed"):
        async with main.lifespan(main.app):
            client = _installed_client()
            real_close = client.aclose

            async def _failing_aclose() -> None:
                await real_close()
                closed.append(True)
                raise RuntimeError("close failed")

            monkeypatch.setattr(client, "aclose", _failing_aclose)

    assert closed == [True]
    assert llm_router._gateway_client_factory is None
