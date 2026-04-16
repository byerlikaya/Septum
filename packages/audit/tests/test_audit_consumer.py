"""AuditConsumer round-trip against a real FileQueueBackend."""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import pytest

pytest.importorskip("septum_queue")

from septum_queue import FileQueueBackend  # noqa: E402

from septum_audit import AuditRecord, JsonlFileSink, MemorySink  # noqa: E402
from septum_audit.consumer import AuditConsumer  # noqa: E402


async def _publish(queue: FileQueueBackend, record: AuditRecord) -> None:
    await queue.publish(record.to_dict())


async def test_consumer_ingests_one_message_into_memory_sink(tmp_path: Path):
    queue = FileQueueBackend(tmp_path / "queue", topic="audit")
    sink = MemorySink()
    consumer = AuditConsumer(queue=queue, sink=sink)

    await _publish(
        queue,
        AuditRecord(source="api", event_type="pii.detected", attributes={"n": 3}),
    )

    processed = await consumer.run_once(block_ms=200)
    assert processed is True
    records = list(sink.read_all())
    assert len(records) == 1
    assert records[0].event_type == "pii.detected"
    assert records[0].attributes == {"n": 3}


async def test_consumer_round_trip_into_jsonl_sink(tmp_path: Path):
    queue = FileQueueBackend(tmp_path / "queue", topic="audit")
    sink = JsonlFileSink(tmp_path / "audit.jsonl")
    consumer = AuditConsumer(queue=queue, sink=sink)

    expected = [
        AuditRecord(source="gateway", event_type="llm.completed", correlation_id="c1"),
        AuditRecord(source="gateway", event_type="llm.failed", correlation_id="c2"),
    ]
    for r in expected:
        await _publish(queue, r)

    for _ in expected:
        assert await consumer.run_once(block_ms=200) is True

    persisted = list(sink.read_all())
    assert [r.correlation_id for r in persisted] == ["c1", "c2"]


async def test_consumer_returns_false_when_queue_is_empty(tmp_path: Path):
    queue = FileQueueBackend(tmp_path / "queue", topic="audit")
    consumer = AuditConsumer(queue=queue, sink=MemorySink())
    processed = await consumer.run_once(block_ms=0)
    assert processed is False


async def test_consumer_drops_malformed_payload_without_crashing(tmp_path: Path):
    queue = FileQueueBackend(tmp_path / "queue", topic="audit")
    sink = MemorySink()
    consumer = AuditConsumer(queue=queue, sink=sink)

    # Publish a payload that survives JSON encoding but trips
    # AuditRecord.from_dict — currently from_dict is permissive enough
    # that the only realistic failure is a non-mapping payload, which
    # our queue contract forbids. Simulate the failure by feeding the
    # consumer a sink that raises on write — the message must be
    # nacked + re-queued, not lost.

    class BoomSink(MemorySink):
        async def write(self, record):  # type: ignore[override]
            raise RuntimeError("disk full")

    consumer = AuditConsumer(queue=queue, sink=BoomSink())
    await _publish(queue, AuditRecord(source="api", event_type="x"))
    processed = await consumer.run_once(block_ms=200)
    assert processed is True

    # Re-queued — a second consumer (or the same one with a working
    # sink) should still find it.
    healthy = AuditConsumer(queue=queue, sink=MemorySink())
    processed_again = await healthy.run_once(block_ms=200)
    assert processed_again is True
