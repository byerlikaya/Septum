"""AuditConsumer: message handling against a real FileQueueBackend and a fake queue, including payload validation, ack and nack, and log hygiene."""

from __future__ import annotations

import logging
import math
from pathlib import Path

import pytest

pytest.importorskip("septum_queue")

from septum_queue import FileQueueBackend  # noqa: E402

from septum_audit import AuditRecord, JsonlFileSink, MemorySink  # noqa: E402
from septum_audit.consumer import AuditConsumer  # noqa: E402
from septum_audit.events import INGEST_FLAG, LedgerIntegrityError, UnstorableRecordError  # noqa: E402

from _helpers import CaptureSink, FakeMessage, FakeQueue, NOW  # noqa: E402


async def _publish(queue: FileQueueBackend, record: AuditRecord) -> None:
    await queue.publish(record.to_dict())


async def test_consumer_ingests_one_message_into_memory_sink(tmp_path: Path):
    queue = FileQueueBackend(tmp_path / "queue", topic="audit")
    sink = MemorySink()
    consumer = AuditConsumer(queue=queue, sink=sink)

    await _publish(queue, AuditRecord(source="api", event_type="pii.detected", attributes={"n": 3}))

    assert await consumer.run_once(block_ms=200) is True
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

    assert [r.correlation_id for r in sink.read_all()] == ["c1", "c2"]


async def test_consumer_returns_false_when_queue_is_empty(tmp_path: Path):
    queue = FileQueueBackend(tmp_path / "queue", topic="audit")
    consumer = AuditConsumer(queue=queue, sink=MemorySink())
    assert await consumer.run_once(block_ms=0) is False


async def test_consumer_nacks_when_sink_write_fails_and_a_healthy_consumer_gets_the_message(tmp_path: Path):
    queue = FileQueueBackend(tmp_path / "queue", topic="audit")

    class BoomSink(MemorySink):
        async def write(self, record):  # type: ignore[override]
            raise OSError("disk full")

    consumer = AuditConsumer(queue=queue, sink=BoomSink())
    await _publish(queue, AuditRecord(source="api", event_type="x"))
    assert await consumer.run_once(block_ms=200) is True

    healthy = AuditConsumer(queue=queue, sink=MemorySink())
    assert await healthy.run_once(block_ms=200) is True


async def test_future_and_non_finite_timestamps_are_kept_and_stamped_at_ingest(tmp_path: Path):
    import time

    queue = FileQueueBackend(tmp_path / "queue", topic="audit")
    sink = JsonlFileSink(tmp_path / "audit.jsonl")
    consumer = AuditConsumer(queue=queue, sink=sink)

    await queue.publish({"id": "future", "timestamp": time.time() + 3600, "source": "s", "event_type": "e"})
    await queue.publish({"id": "nan", "timestamp": float("nan"), "source": "s", "event_type": "e"})
    await queue.publish({"id": "ok", "timestamp": time.time() + 60, "source": "s", "event_type": "e"})

    for _ in range(3):
        await consumer.run_once(block_ms=200)

    records = list(sink.read_all())
    assert [r.id for r in records] == ["future", "nan", "ok"]
    assert records[0].timestamp > time.time()
    assert records[1].attributes[INGEST_FLAG] is True
    assert all(r.received_at is not None and r.received_at <= time.time() for r in records)
    sink.verify()


async def test_nan_attribute_ends_in_exactly_one_write_and_one_ack(tmp_path: Path):
    queue = FileQueueBackend(tmp_path / "queue", topic="audit")
    acks: list[str] = []
    real_ack = queue.ack

    async def _counting_ack(message_id: str) -> None:
        acks.append(message_id)
        await real_ack(message_id)

    queue.ack = _counting_ack
    sink = JsonlFileSink(tmp_path / "audit.jsonl")
    await queue.publish({
        "id": "nan-attr", "timestamp": NOW, "source": "s", "event_type": "e",
        "attributes": {"score": float("nan"), "nested": [float("inf")], INGEST_FLAG: False},
    })
    consumer = AuditConsumer(queue=queue, sink=sink)

    assert await consumer.run_once(block_ms=200) is True
    assert await consumer.run_once(block_ms=200) is False

    records = list(sink.read_all())
    assert len(records) == 1
    assert records[0].attributes["score"] == "NaN"
    assert records[0].attributes["nested"] == ["Infinity"]
    assert records[0].attributes[INGEST_FLAG] is True
    assert len(acks) == 1
    sink.verify()


async def test_depth_2000_attributes_give_one_write_and_one_ack(tmp_path: Path):
    deep: object = "leaf"
    for _ in range(2000):
        deep = {"n": deep}
    payload = {"id": "deep", "timestamp": NOW, "source": "s", "event_type": "e", "attributes": {"deep": deep}}
    queue = FakeQueue([FakeMessage(payload, "m-1")])
    sink = JsonlFileSink(tmp_path / "audit.jsonl")

    assert await AuditConsumer(queue=queue, sink=sink).run_once(block_ms=10) is True

    records = list(sink.read_all())
    assert len(records) == 1
    assert queue.acked == ["m-1"] and queue.nacked == []
    assert records[0].attributes[INGEST_FLAG] is True
    sink.verify()


async def test_consumer_acks_a_malformed_payload_without_writing_it():
    queue = FakeQueue([FakeMessage(["not a dict"], "bad")])
    sink = CaptureSink()

    assert await AuditConsumer(queue=queue, sink=sink).run_once(block_ms=1) is True

    assert queue.acked == ["bad"]
    assert queue.nacked == []
    assert sink.records == []


async def test_consumer_ignores_a_producer_supplied_received_at():
    payload = AuditRecord(id="x", source="s", event_type="e").to_dict()
    payload["received_at"] = 123.0
    queue = FakeQueue([FakeMessage(payload, "ok")])
    sink = CaptureSink()

    await AuditConsumer(queue=queue, sink=sink).run_once(block_ms=1)

    assert sink.records[0].received_at is None
    assert queue.acked == ["ok"]


class _Scripted:
    """Each write pops one outcome: an exception is raised, ``None`` keeps the record."""

    def __init__(self, *outcomes: Exception | None) -> None:
        self.records: list[AuditRecord] = []
        self._outcomes = list(outcomes)

    async def write(self, record: AuditRecord) -> None:
        outcome = self._outcomes.pop(0) if self._outcomes else None
        if outcome is not None:
            raise outcome
        self.records.append(record)


class _Endless:
    """Every consume hands out one message, so a failing message is redelivered forever."""

    def __init__(self, payload) -> None:
        self._payload = payload
        self.consumes = 0
        self.acked: list[str] = []
        self.nacked: list[bool] = []

    async def consume(self, *, batch_size, block_ms):
        self.consumes += 1
        yield FakeMessage(self._payload, "m")

    async def ack(self, message_id: str) -> None:
        self.acked.append(message_id)

    async def nack(self, message_id: str, *, requeue: bool = True) -> None:
        self.nacked.append(requeue)


async def _no_sleep(seconds: float) -> None:
    return None


def _record_payload() -> dict:
    return AuditRecord(id="x", source="s", event_type="e").to_dict()


@pytest.mark.parametrize(
    "error",
    [
        OSError("disk full"),
        LedgerIntegrityError("ledger chain broken at line 3"),
        ValueError("bad value"),
        TypeError("bad type"),
        RuntimeError("anything else"),
    ],
    ids=["io error", "damaged ledger", "value error", "type error", "runtime error"],
)
async def test_every_failure_except_an_unstorable_record_requeues(error: Exception):
    queue = FakeQueue([FakeMessage(_record_payload(), "m")])

    await AuditConsumer(queue=queue, sink=CaptureSink(error), sleep=_no_sleep).run_once(block_ms=1)

    assert queue.acked == []
    assert queue.nacked == [("m", True)]


async def test_an_unstorable_record_is_kept_as_a_placeholder_and_acked():
    payload = AuditRecord(
        id="x" * 80, source="s", event_type="e", correlation_id="c", attributes={"secret": "v"}
    ).to_dict()
    queue = FakeQueue([FakeMessage(payload, "m")])
    sink = _Scripted(UnstorableRecordError("would not verify"))

    await AuditConsumer(queue=queue, sink=sink, sleep=_no_sleep).run_once(block_ms=1)

    assert queue.acked == ["m"]
    assert queue.nacked == []
    (placeholder,) = sink.records
    assert placeholder.id == "x" * 64
    assert (placeholder.source, placeholder.event_type, placeholder.correlation_id) == ("s", "e", "c")
    assert placeholder.attributes == {"value": "<unstorable>"}
    assert math.isnan(placeholder.timestamp)
    assert "secret" not in repr(placeholder.attributes)


async def test_a_placeholder_that_cannot_be_written_requeues_and_is_not_acked():
    queue = FakeQueue([FakeMessage(_record_payload(), "m")])
    sink = _Scripted(UnstorableRecordError("would not verify"), OSError("disk full"))

    await AuditConsumer(queue=queue, sink=sink, sleep=_no_sleep).run_once(block_ms=1)

    assert queue.acked == []
    assert queue.nacked == [("m", True)]


async def test_a_placeholder_written_to_a_real_sink_reads_back_flagged_with_an_ingest_time(tmp_path: Path):
    sink = JsonlFileSink(tmp_path / "audit.jsonl")
    await sink.write(AuditRecord(id="x", timestamp=math.nan, source="s", event_type="e", attributes={"value": "<unstorable>"}))

    (record,) = sink.read_all()
    assert record.attributes[INGEST_FLAG] is True
    assert math.isfinite(record.timestamp) and record.timestamp != 0
    sink.verify()


async def test_failed_writes_back_off_doubling_and_are_capped_at_sixty_seconds():
    delays: list[float] = []

    class _Stop(Exception):
        pass

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)
        if len(delays) == 8:
            raise _Stop

    queue = _Endless(_record_payload())
    consumer = AuditConsumer(queue=queue, sink=CaptureSink(OSError("disk full")), sleep=fake_sleep)

    with pytest.raises(_Stop):
        await consumer.run_forever(block_ms=1)

    assert delays == [2, 4, 8, 16, 32, 60, 60, 60]
    assert queue.consumes == 8
    assert queue.nacked == [True] * 8


async def test_a_successful_write_resets_the_backoff():
    delays: list[float] = []

    class _Stop(Exception):
        pass

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)
        if len(delays) == 3:
            raise _Stop

    queue = _Endless(_record_payload())
    sink = _Scripted(OSError("first"), OSError("second"), None, OSError("after success"))
    consumer = AuditConsumer(queue=queue, sink=sink, sleep=fake_sleep)

    with pytest.raises(_Stop):
        await consumer.run_forever(block_ms=1)

    assert delays == [2, 4, 2]
    assert queue.acked == ["m"]


async def test_failure_log_is_rate_limited_and_recovery_is_logged(caplog: pytest.LogCaptureFixture):
    queue = _Endless(_record_payload())
    sink = _Scripted(*([OSError("disk full")] * 12))
    consumer = AuditConsumer(queue=queue, sink=sink, sleep=_no_sleep)

    with caplog.at_level(logging.INFO, logger="septum_audit.consumer"):
        for _ in range(13):
            await consumer.run_once(block_ms=1)

    failures = [r.getMessage() for r in caplog.records if "will retry" in r.getMessage()]
    assert len(failures) == 2
    assert "consecutive_failures=1 " in failures[0]
    assert "consecutive_failures=10 " in failures[1]
    recovered = [r.getMessage() for r in caplog.records if "recovered" in r.getMessage()]
    assert recovered == ["audit consumer recovered after 12 failed attempts"]


async def test_a_stored_record_with_no_prior_failure_logs_no_recovery(caplog: pytest.LogCaptureFixture):
    queue = _Endless(_record_payload())
    consumer = AuditConsumer(queue=queue, sink=CaptureSink(), sleep=_no_sleep)

    with caplog.at_level(logging.INFO, logger="septum_audit.consumer"):
        await consumer.run_once(block_ms=1)
        await consumer.run_once(block_ms=1)

    assert not [r for r in caplog.records if "recovered" in r.getMessage()]


async def test_a_stored_placeholder_resets_the_backoff_and_logs_recovery(caplog: pytest.LogCaptureFixture):
    delays: list[float] = []

    class _Stop(Exception):
        pass

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)
        if len(delays) == 3:
            raise _Stop

    queue = _Endless(_record_payload())
    sink = _Scripted(OSError("first"), OSError("second"), UnstorableRecordError("no"), None, OSError("later"))
    consumer = AuditConsumer(queue=queue, sink=sink, sleep=fake_sleep)

    with caplog.at_level(logging.INFO, logger="septum_audit.consumer"):
        with pytest.raises(_Stop):
            await consumer.run_forever(block_ms=1)

    assert delays == [2, 4, 2]
    assert queue.acked == ["m"]
    assert [r.getMessage() for r in caplog.records if "recovered" in r.getMessage()] == [
        "audit consumer recovered after 2 failed attempts"
    ]


async def test_a_failed_placeholder_write_counts_toward_the_backoff_and_is_logged(
    caplog: pytest.LogCaptureFixture,
):
    delays: list[float] = []

    class _Stop(Exception):
        pass

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)
        if len(delays) == 2:
            raise _Stop

    queue = _Endless(_record_payload())
    sink = _Scripted(UnstorableRecordError("no"), OSError("disk full"), UnstorableRecordError("no"), OSError("again"))
    consumer = AuditConsumer(queue=queue, sink=sink, sleep=fake_sleep)

    with caplog.at_level(logging.INFO, logger="septum_audit.consumer"):
        with pytest.raises(_Stop):
            await consumer.run_forever(block_ms=1)

    assert delays == [2, 4]
    assert queue.acked == []
    assert queue.nacked == [True, True]
    assert any("will retry" in r.getMessage() and "consecutive_failures=1 " in r.getMessage() for r in caplog.records)


async def test_an_unstorable_record_does_not_start_a_backoff():
    queue = _Endless(_record_payload())
    consumer = AuditConsumer(
        queue=queue, sink=_Scripted(UnstorableRecordError("no")), sleep=_no_sleep
    )

    await consumer.run_once(block_ms=1)

    assert consumer._failures == 0


async def test_a_stored_record_is_acked_and_never_nacked():
    payload = AuditRecord(id="x", source="s", event_type="e").to_dict()
    queue = FakeQueue([FakeMessage(payload, "m")])

    await AuditConsumer(queue=queue, sink=CaptureSink()).run_once(block_ms=1)

    assert queue.acked == ["m"]
    assert queue.nacked == []


async def test_sink_drops_the_producer_copy_of_the_flag_the_consumer_passes_through(tmp_path: Path):
    payload = {"id": "x", "source": "s", "event_type": "e", "timestamp": 1_700_000_000.0, "received_at": 5.0,
               "attributes": {INGEST_FLAG: True, "keep": 1}}
    queue = FakeQueue([FakeMessage(payload, "m1")])
    sink = JsonlFileSink(tmp_path / "audit.jsonl")

    assert await AuditConsumer(queue=queue, sink=sink).run_once() is True
    (record,) = sink.read_all()
    assert record.attributes == {"keep": 1}


async def test_consumer_logs_the_record_id_with_repr_so_it_cannot_forge_a_log_line(caplog: pytest.LogCaptureFixture):
    hostile = "evil\nERROR forged line"
    queue = FakeQueue([FakeMessage({"id": hostile, "source": "s", "event_type": "e"}, "m1")])

    with caplog.at_level("ERROR", logger="septum_audit.consumer"):
        await AuditConsumer(queue=queue, sink=CaptureSink(OSError("disk full"))).run_once()

    messages = [r.getMessage() for r in caplog.records if r.name == "septum_audit.consumer"]
    assert len(messages) == 1
    assert "\n" not in messages[0]
    assert repr(hostile) in messages[0]
    assert "OSError" in messages[0] and "disk full" not in messages[0]
    assert queue.nacked == [("m1", True)]


async def test_malformed_payload_log_quotes_and_cuts_the_queue_id(caplog: pytest.LogCaptureFixture):
    long_id = "q" * 200
    queue = FakeQueue([FakeMessage(["not a dict"], long_id)])

    with caplog.at_level(logging.ERROR, logger="septum_audit.consumer"):
        await AuditConsumer(queue=queue, sink=CaptureSink()).run_once(block_ms=10)

    message = " ".join(r.getMessage() for r in caplog.records)
    assert repr("q" * 64) in message
    assert "q" * 65 not in message
