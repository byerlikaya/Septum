"""GatewayConsumer audit-hook tests.

When the consumer is wired with an ``audit_queue``, every handled message
must produce a PII-free envelope on the audit topic. The opposite is
also tested: with no audit queue, no audit events are emitted (the
existing zero-overhead default for stdlib-only deployments).
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from septum_gateway import ForwarderRegistry, GatewayConsumer, GatewayError
from septum_queue import (
    FileQueueBackend,
    RequestEnvelope,
)


class _FakeForwarder:
    def __init__(self, *, text: str | None = None, error: Exception | None = None):
        self.text = text
        self.error = error

    async def complete(self, envelope: RequestEnvelope) -> str:
        if self.error is not None:
            raise self.error
        return self.text or ""


async def _drain(queue: FileQueueBackend, *, count: int) -> list[dict]:
    drained: list[dict] = []
    async for message in queue.consume(batch_size=count, block_ms=200):
        drained.append(dict(message.payload))
        if len(drained) == count:
            break
    return drained


class TestGatewayAuditHook:
    async def test_no_audit_queue_emits_nothing(self, tmp_path: Path):
        req_q = FileQueueBackend(tmp_path, topic="req")
        resp_q = FileQueueBackend(tmp_path, topic="resp")
        registry = ForwarderRegistry(anthropic=_FakeForwarder(text="hi"))
        consumer = GatewayConsumer(
            request_queue=req_q, response_queue=resp_q, registry=registry
        )
        envelope = RequestEnvelope.new(
            provider="anthropic",
            model="claude-3",
            messages=[{"role": "user", "content": "hi"}],
        )
        await req_q.publish(asdict(envelope))
        await consumer.run_once(block_ms=0)
        # No audit_queue => nothing raised, no extra side effects.

    async def test_successful_request_emits_completed_envelope(self, tmp_path: Path):
        req_q = FileQueueBackend(tmp_path, topic="req")
        resp_q = FileQueueBackend(tmp_path, topic="resp")
        audit_q = FileQueueBackend(tmp_path, topic="audit")
        registry = ForwarderRegistry(
            openai=_FakeForwarder(text="[PERSON_1] is in [CITY_1].")
        )
        consumer = GatewayConsumer(
            request_queue=req_q,
            response_queue=resp_q,
            registry=registry,
            audit_queue=audit_q,
        )
        envelope = RequestEnvelope.new(
            provider="openai",
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You answer briefly."},
                {"role": "user", "content": "Where is [PERSON_1]?"},
            ],
            max_tokens=128,
        )
        await req_q.publish(asdict(envelope))
        await consumer.run_once(block_ms=0)

        events = await _drain(audit_q, count=1)
        assert len(events) == 1
        evt = events[0]
        assert evt["source"] == "septum-gateway"
        assert evt["event_type"] == "llm.request.completed"
        assert evt["correlation_id"] == envelope.correlation_id

        attrs = evt["attributes"]
        assert attrs["provider"] == "openai"
        assert attrs["model"] == "gpt-4o-mini"
        assert attrs["status"] == "ok"
        assert attrs["message_count"] == 2
        assert attrs["max_tokens"] == 128
        assert isinstance(attrs["latency_ms"], (int, float))
        assert attrs["latency_ms"] >= 0

        # PII discipline: no prompt content, no response text, no api key.
        assert "messages" not in attrs
        assert "text" not in attrs
        assert "api_key" not in attrs
        assert "base_url" not in attrs
        assert "error" not in attrs

    async def test_forwarder_error_emits_failed_envelope_with_error_field(
        self, tmp_path: Path
    ):
        req_q = FileQueueBackend(tmp_path, topic="req")
        resp_q = FileQueueBackend(tmp_path, topic="resp")
        audit_q = FileQueueBackend(tmp_path, topic="audit")
        registry = ForwarderRegistry(
            anthropic=_FakeForwarder(error=GatewayError("upstream 502"))
        )
        consumer = GatewayConsumer(
            request_queue=req_q,
            response_queue=resp_q,
            registry=registry,
            audit_queue=audit_q,
        )
        envelope = RequestEnvelope.new(
            provider="anthropic",
            model="claude-3",
            messages=[{"role": "user", "content": "hi"}],
        )
        await req_q.publish(asdict(envelope))
        await consumer.run_once(block_ms=0)

        events = await _drain(audit_q, count=1)
        attrs = events[0]["attributes"]
        assert events[0]["event_type"] == "llm.request.failed"
        assert attrs["status"] == "error"
        assert attrs["error"] == "upstream 502"

    async def test_audit_publish_failure_does_not_break_main_path(
        self, tmp_path: Path, caplog
    ):
        """A broken audit queue must never block the request/response loop."""

        class BrokenQueue:
            topic = "broken-audit"

            async def publish(self, payload):
                raise RuntimeError("disk full")

            async def consume(self, *, batch_size=1, block_ms=None):
                if False:
                    yield None  # pragma: no cover

            async def ack(self, message_id):
                return None

            async def nack(self, message_id, *, requeue=True):
                return None

            async def close(self):
                return None

        req_q = FileQueueBackend(tmp_path, topic="req")
        resp_q = FileQueueBackend(tmp_path, topic="resp")
        registry = ForwarderRegistry(anthropic=_FakeForwarder(text="ok"))
        consumer = GatewayConsumer(
            request_queue=req_q,
            response_queue=resp_q,
            registry=registry,
            audit_queue=BrokenQueue(),
        )
        envelope = RequestEnvelope.new(
            provider="anthropic",
            model="claude-3",
            messages=[{"role": "user", "content": "hi"}],
        )
        await req_q.publish(asdict(envelope))

        with caplog.at_level("WARNING", logger="septum_gateway.response_handler"):
            processed = await consumer.run_once(block_ms=0)

        assert processed is True
        assert any("audit publish failed" in r.message for r in caplog.records)

        # The response envelope still went out — main path is unaffected.
        async for message in resp_q.consume(batch_size=1, block_ms=200):
            assert message.payload["correlation_id"] == envelope.correlation_id
            break
        else:  # pragma: no cover
            raise AssertionError("response queue stayed empty")
