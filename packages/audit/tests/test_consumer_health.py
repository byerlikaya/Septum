"""Consumer heartbeat sidecar, its effect on /health, placeholder marking, and payload shape."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from septum_audit import AuditRecord, JsonlFileSink
from septum_audit import consumer as consumer_module
from septum_audit.consumer import AuditConsumer
from septum_audit.consumer_status import ConsumerStatus, consumer_status_path, read_consumer_status, write_consumer_status
from septum_audit.events import INGEST_UNSTORABLE, UnstorableRecordError
from septum_audit.main import _consumer_state

from _helpers import CaptureSink, FakeMessage, FakeQueue


class _FailingLedgerSink:
    """Fails every write, but reports a ledger path so the consumer publishes its sidecar there."""

    def __init__(self, path: Path) -> None:
        self.path = path

    async def write(self, record) -> None:
        raise OSError("disk full")

    def read_all(self):
        return iter(())

    async def close(self) -> None:
        return None


async def _no_sleep(seconds: float) -> None:
    return None


def _payload(record_id: str = "x") -> dict:
    return AuditRecord(id=record_id, source="s", event_type="e").to_dict()


async def test_failing_writes_mark_the_consumer_failing_and_recovery_clears_it(tmp_path: Path):
    ledger = tmp_path / "audit.jsonl"
    clock = [1_000.0]
    queue = FakeQueue([FakeMessage(_payload(), "m") for _ in range(3)])
    consumer = AuditConsumer(queue=queue, sink=_FailingLedgerSink(ledger), sleep=_no_sleep, clock=lambda: clock[0])

    for _ in range(3):
        await consumer.run_once(block_ms=1)

    status = read_consumer_status(ledger)
    assert status is not None
    assert status.consecutive_failures == 3
    assert status.first_failure_at == 1_000.0
    assert _consumer_state(ledger, clock[0])[1] is False
    assert _consumer_state(ledger, clock[0])[0] == "consumer_failing"

    healthy_sink = JsonlFileSink(ledger, clock=lambda: clock[0])
    recovering = AuditConsumer(queue=FakeQueue([FakeMessage(_payload("ok"), "r")]), sink=healthy_sink, clock=lambda: clock[0])
    await recovering.run_once(block_ms=1)

    assert read_consumer_status(ledger).consecutive_failures == 0
    assert _consumer_state(ledger, clock[0]) == ("ok", True)


def test_a_failure_streak_older_than_five_minutes_fails_health_even_below_three(tmp_path: Path):
    ledger = tmp_path / "audit.jsonl"
    now = time.time()
    write_consumer_status(ledger, ConsumerStatus(None, 1, now - 400, now))

    assert _consumer_state(ledger, now) == ("consumer_failing", False)


def test_a_missing_consumer_sidecar_does_not_fail_health(tmp_path: Path):
    assert _consumer_state(tmp_path / "audit.jsonl", time.time()) == ("not_reported", True)


def test_an_old_sidecar_with_no_failure_streak_is_healthy(tmp_path: Path):
    ledger = tmp_path / "audit.jsonl"
    now = time.time()
    write_consumer_status(ledger, ConsumerStatus(now - 7200, 0, None, now - 7200))

    assert _consumer_state(ledger, now) == ("ok", True)


def test_a_malformed_consumer_sidecar_fails_health(tmp_path: Path):
    ledger = tmp_path / "audit.jsonl"
    consumer_status_path(ledger).write_text("{not json", encoding="utf-8")

    assert _consumer_state(ledger, time.time()) == ("consumer_unreadable", False)


def test_health_endpoint_reports_consumer_failing_then_recovery(tmp_path: Path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from septum_audit.config import AuditConfig
    from septum_audit.main import create_app

    ledger = tmp_path / "audit.jsonl"
    now = time.time()
    client = TestClient(create_app(AuditConfig(sink_path=str(ledger)), sink=JsonlFileSink(ledger)))

    write_consumer_status(ledger, ConsumerStatus(None, 3, now, now))
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["consumer"] == "consumer_failing"

    write_consumer_status(ledger, ConsumerStatus(now, 0, None, now))
    assert client.get("/health").status_code == 200


async def test_a_real_placeholder_is_marked_and_a_forged_one_is_not(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    real = JsonlFileSink(tmp_path / "real.jsonl")
    original_append = real._append
    calls = [0]

    def _refuse_first(record):
        calls[0] += 1
        if calls[0] == 1:
            raise UnstorableRecordError("would not verify")
        return original_append(record)

    monkeypatch.setattr(real, "_append", _refuse_first)
    queue = FakeQueue([FakeMessage(_payload("evt"), "m")])
    await AuditConsumer(queue=queue, sink=real, sleep=_no_sleep).run_once(block_ms=1)
    (placeholder,) = real.read_all()

    forged_payload = _payload("evt2")
    forged_payload["attributes"] = {"value": "<unstorable>", INGEST_UNSTORABLE: True}
    forged = JsonlFileSink(tmp_path / "forged.jsonl")
    await AuditConsumer(queue=FakeQueue([FakeMessage(forged_payload, "f")]), sink=forged, sleep=_no_sleep).run_once(block_ms=1)
    (forged_record,) = forged.read_all()

    assert placeholder.attributes[INGEST_UNSTORABLE] is True
    assert INGEST_UNSTORABLE not in forged_record.attributes
    assert forged_record.attributes == {"value": "<unstorable>"}


async def test_a_list_of_pairs_payload_takes_the_malformed_path_and_writes_nothing():
    sink = CaptureSink()
    queue = FakeQueue([FakeMessage([["source", "s"], ["event_type", "e"]], "m")])

    await AuditConsumer(queue=queue, sink=sink, sleep=_no_sleep).run_once(block_ms=1)

    assert queue.acked == ["m"]
    assert queue.nacked == []
    assert sink.records == []


async def test_successful_stores_publish_the_heartbeat_at_most_every_ten_seconds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ledger = tmp_path / "audit.jsonl"
    writes: list[float] = []
    real_write = consumer_module.write_consumer_status

    def _count(path, status):
        writes.append(status.checked_at)
        real_write(path, status)

    monkeypatch.setattr(consumer_module, "write_consumer_status", _count)
    clock = [100.0]
    sink = JsonlFileSink(ledger, clock=lambda: clock[0])
    consumer = AuditConsumer(queue=FakeQueue([]), sink=sink, sleep=_no_sleep, clock=lambda: clock[0])

    for at, record_id in [(100.0, "a"), (101.0, "b"), (102.0, "c"), (111.0, "d")]:
        clock[0] = at
        consumer._queue = FakeQueue([FakeMessage(_payload(record_id), record_id)])
        await consumer.run_once(block_ms=1)

    assert writes == [100.0, 111.0]


async def test_a_failing_heartbeat_write_does_not_stop_ingest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def _broken(path, status):
        raise OSError("read-only volume")

    monkeypatch.setattr(consumer_module, "write_consumer_status", _broken)
    sink = JsonlFileSink(tmp_path / "audit.jsonl")
    queue = FakeQueue([FakeMessage(_payload("a"), "m")])

    await AuditConsumer(queue=queue, sink=sink, sleep=_no_sleep).run_once(block_ms=1)

    assert queue.acked == ["m"]
    assert [r.id for r in sink.read_all()] == ["a"]
