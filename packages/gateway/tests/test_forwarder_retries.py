"""Characterization tests for the forwarder retry loop: transport errors, backoff, and error mapping."""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest
import respx
from septum_gateway import GatewayError, OpenAIForwarder
from septum_gateway import forwarder as forwarder_module
from septum_queue import RequestEnvelope

_URL = "https://api.openai.com/v1/chat/completions"
_OK = httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})


def _env() -> RequestEnvelope:
    return RequestEnvelope.new(
        provider="openai",
        model="test-model",
        messages=[{"role": "user", "content": "hello"}],
    )


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []

    async def record_sleep(delay, *args, **kwargs):
        recorded.append(delay)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    return recorded


@respx.mock
async def test_transport_error_is_retried_then_succeeds(sleeps: list[float]) -> None:
    route = respx.post(_URL).mock(side_effect=[httpx.ConnectError("boom"), _OK])
    forwarder = OpenAIForwarder(api_key="sk", max_attempts=2, base_backoff_seconds=1.0)
    assert await forwarder.complete(_env()) == "ok"
    assert route.call_count == 2
    assert len(sleeps) == 1
    assert 0.0 <= sleeps[0] <= 1.0
    await forwarder.aclose()


@respx.mock
async def test_transport_errors_on_every_attempt_fail_without_a_trailing_sleep(
    sleeps: list[float],
) -> None:
    route = respx.post(_URL).mock(side_effect=httpx.ConnectError("boom"))
    forwarder = OpenAIForwarder(api_key="sk", max_attempts=3, base_backoff_seconds=1.0)
    with pytest.raises(GatewayError, match="failed after 3 attempts") as excinfo:
        await forwarder.complete(_env())
    assert route.call_count == 3
    assert isinstance(excinfo.value.__cause__, httpx.ConnectError)
    assert len(sleeps) == 2
    assert 0.0 <= sleeps[0] <= 1.0
    assert 0.0 <= sleeps[1] <= 2.0
    await forwarder.aclose()


@respx.mock
async def test_5xx_on_every_attempt_maps_last_status_into_the_cause(
    sleeps: list[float],
) -> None:
    route = respx.post(_URL).respond(503, json={"error": "down"})
    forwarder = OpenAIForwarder(api_key="sk", max_attempts=3, base_backoff_seconds=0.5)
    with pytest.raises(GatewayError, match="failed after 3 attempts") as excinfo:
        await forwarder.complete(_env())
    assert route.call_count == 3
    cause = excinfo.value.__cause__
    assert isinstance(cause, httpx.HTTPStatusError)
    assert cause.response.status_code == 503
    assert len(sleeps) == 2
    await forwarder.aclose()


@respx.mock
async def test_retry_after_is_honored_on_a_5xx(sleeps: list[float]) -> None:
    respx.post(_URL).mock(
        side_effect=[httpx.Response(503, headers={"Retry-After": "2"}), _OK]
    )
    forwarder = OpenAIForwarder(api_key="sk", max_attempts=2, base_backoff_seconds=10.0)
    assert await forwarder.complete(_env()) == "ok"
    assert sleeps == [2.0]
    await forwarder.aclose()


@respx.mock
async def test_missing_retry_after_falls_back_to_bounded_jitter(sleeps: list[float]) -> None:
    respx.post(_URL).mock(side_effect=[httpx.Response(429), httpx.Response(429), _OK])
    forwarder = OpenAIForwarder(api_key="sk", max_attempts=3, base_backoff_seconds=0.5)
    assert await forwarder.complete(_env()) == "ok"
    assert len(sleeps) == 2
    assert 0.0 <= sleeps[0] <= 0.5
    assert 0.0 <= sleeps[1] <= 1.0
    await forwarder.aclose()


@respx.mock
async def test_non_retryable_4xx_is_logged_and_mapped_without_sleeping(
    sleeps: list[float], caplog: pytest.LogCaptureFixture
) -> None:
    route = respx.post(_URL).respond(401, json={"error": "nope"})
    forwarder = OpenAIForwarder(api_key="sk", max_attempts=3, base_backoff_seconds=0.5)
    with caplog.at_level(logging.ERROR, logger=forwarder_module.logger.name):
        with pytest.raises(GatewayError, match="status=401") as excinfo:
            await forwarder.complete(_env())
    assert route.call_count == 1
    assert sleeps == []
    assert isinstance(excinfo.value.__cause__, httpx.HTTPStatusError)
    assert "non-retryable status" in caplog.text
    await forwarder.aclose()


@respx.mock
async def test_idempotency_key_is_the_same_on_every_attempt(sleeps: list[float]) -> None:
    route = respx.post(_URL).mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(500), _OK]
    )
    forwarder = OpenAIForwarder(api_key="sk", max_attempts=3, base_backoff_seconds=0.5)
    envelope = _env()
    assert await forwarder.complete(envelope) == "ok"
    keys = {call.request.headers["Idempotency-Key"] for call in route.calls}
    assert keys == {envelope.correlation_id}
    await forwarder.aclose()
