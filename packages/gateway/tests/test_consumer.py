"""End-to-end GatewayConsumer tests using FileQueueBackend + fake forwarder."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest

from septum_gateway import ForwarderRegistry, GatewayConsumer, GatewayError
from septum_queue import (
    FileQueueBackend,
    RequestEnvelope,
    ResponseEnvelope,
)


class _FakeForwarder:
    """Minimal forwarder used to drive the consumer without httpx."""

    def __init__(self, *, text: str | None = None, error: Exception | None = None):
        self.text = text
        self.error = error
        self.calls: list[RequestEnvelope] = []

    async def complete(self, envelope: RequestEnvelope) -> str:
        self.calls.append(envelope)
        if self.error is not None:
            raise self.error
        return self.text or ""


async def _drain_response(
    response_queue: FileQueueBackend,
) -> ResponseEnvelope:
    async for message in response_queue.consume(batch_size=1, block_ms=1000):
        return ResponseEnvelope.from_dict(message.payload)
    raise AssertionError("response queue did not yield a message")


class TestGatewayConsumerRoundTrip:
    async def test_successful_request_publishes_text_response(self, tmp_path: Path):
        req_q = FileQueueBackend(tmp_path, topic="req")
        resp_q = FileQueueBackend(tmp_path, topic="resp")
        forwarder = _FakeForwarder(text="[PERSON_1] is in [CITY_1].")
        registry = ForwarderRegistry(anthropic=forwarder)
        consumer = GatewayConsumer(
            request_queue=req_q, response_queue=resp_q, registry=registry
        )
        try:
            envelope = RequestEnvelope.new(
                provider="anthropic",
                model="claude-3",
                messages=[{"role": "user", "content": "Where is [PERSON_1]?"}],
            )
            await req_q.publish(asdict(envelope))

            processed = await consumer.run_once(block_ms=0)
            assert processed is True

            response = await _drain_response(resp_q)
            assert response.correlation_id == envelope.correlation_id
            assert response.text == "[PERSON_1] is in [CITY_1]."
            assert response.error is None
            assert response.provider == "anthropic"
            assert forwarder.calls[0].messages == envelope.messages
        finally:
            await req_q.close()
            await resp_q.close()

    async def test_forwarder_error_publishes_error_response(self, tmp_path: Path):
        req_q = FileQueueBackend(tmp_path, topic="req")
        resp_q = FileQueueBackend(tmp_path, topic="resp")
        forwarder = _FakeForwarder(error=GatewayError("upstream 502"))
        registry = ForwarderRegistry(openai=forwarder)
        consumer = GatewayConsumer(
            request_queue=req_q, response_queue=resp_q, registry=registry
        )
        try:
            envelope = RequestEnvelope.new(
                provider="openai",
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "hi"}],
            )
            await req_q.publish(asdict(envelope))
            await consumer.run_once(block_ms=0)
            response = await _drain_response(resp_q)
            assert response.text is None
            assert response.error == "upstream 502"
        finally:
            await req_q.close()
            await resp_q.close()

    async def test_unknown_provider_publishes_error_response(self, tmp_path: Path):
        req_q = FileQueueBackend(tmp_path, topic="req")
        resp_q = FileQueueBackend(tmp_path, topic="resp")
        registry = ForwarderRegistry()  # no providers registered
        consumer = GatewayConsumer(
            request_queue=req_q, response_queue=resp_q, registry=registry
        )
        try:
            envelope = RequestEnvelope.new(
                provider="cohere",
                model="command-r",
                messages=[{"role": "user", "content": "hi"}],
            )
            await req_q.publish(asdict(envelope))
            await consumer.run_once(block_ms=0)
            response = await _drain_response(resp_q)
            assert "unsupported provider" in (response.error or "")
        finally:
            await req_q.close()
            await resp_q.close()

    async def test_malformed_payload_is_acked_without_response(
        self, tmp_path: Path
    ):
        """A payload missing required fields must not crash the loop."""
        req_q = FileQueueBackend(tmp_path, topic="req")
        resp_q = FileQueueBackend(tmp_path, topic="resp")
        registry = ForwarderRegistry(anthropic=_FakeForwarder(text="never"))
        consumer = GatewayConsumer(
            request_queue=req_q, response_queue=resp_q, registry=registry
        )
        try:
            await req_q.publish({"not_a_valid_envelope": True})
            processed = await consumer.run_once(block_ms=0)
            assert processed is True
            # Response queue must stay empty — without a correlation id
            # there is no envelope to publish.
            no_messages = True
            async for _ in resp_q.consume(batch_size=1, block_ms=0):
                no_messages = False
            assert no_messages
        finally:
            await req_q.close()
            await resp_q.close()

    async def test_unexpected_exception_captured_as_error_envelope(
        self, tmp_path: Path
    ):
        req_q = FileQueueBackend(tmp_path, topic="req")
        resp_q = FileQueueBackend(tmp_path, topic="resp")
        forwarder = _FakeForwarder(error=RuntimeError("httpx died"))
        registry = ForwarderRegistry(anthropic=forwarder)
        consumer = GatewayConsumer(
            request_queue=req_q, response_queue=resp_q, registry=registry
        )
        try:
            envelope = RequestEnvelope.new(
                provider="anthropic",
                model="claude-3",
                messages=[{"role": "user", "content": "hi"}],
            )
            await req_q.publish(asdict(envelope))
            await consumer.run_once(block_ms=0)
            response = await _drain_response(resp_q)
            assert response.text is None
            assert "unexpected gateway error" in (response.error or "")
            assert "RuntimeError" in (response.error or "")
        finally:
            await req_q.close()
            await resp_q.close()


class TestCreateApp:
    def test_health_endpoint_returns_topics(self):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from septum_gateway import GatewayConfig, create_app

        app = create_app(
            GatewayConfig(
                request_topic="custom.req",
                response_topic="custom.resp",
            )
        )
        with TestClient(app) as client:
            resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["service"] == "septum-gateway"
        assert body["request_topic"] == "custom.req"
        assert body["response_topic"] == "custom.resp"
        assert body["audit_topic"] is None
