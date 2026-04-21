"""AuditConsumer round-trip against a real FileQueueBackend."""

from __future__ import annotations

from pathlib import Path

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


async def test_consumer_nacks_when_sink_write_fails(tmp_path: Path):
    queue = FileQueueBackend(tmp_path / "queue", topic="audit")

    class BoomSink(MemorySink):
        async def write(self, record):  # type: ignore[override]
            raise RuntimeError("disk full")

    consumer = AuditConsumer(queue=queue, sink=BoomSink())
    await _publish(queue, AuditRecord(source="api", event_type="x"))
    assert await consumer.run_once(block_ms=200) is True

    # Re-queued — a healthy sink still sees it.
    healthy = AuditConsumer(queue=queue, sink=MemorySink())
    assert await healthy.run_once(block_ms=200) is True
