"""Characterization tests for FileQueueBackend.consume batching, polling and deadlines."""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path

import pytest
from septum_queue import FileQueueBackend, QueueError


def _incoming(root: Path, topic: str) -> Path:
    return root / topic / "incoming"


@pytest.mark.asyncio
async def test_batch_size_caps_delivery_and_leaves_the_rest_unclaimed(tmp_path: Path) -> None:
    backend = FileQueueBackend(tmp_path, topic="batch")
    try:
        for index in range(5):
            await backend.publish({"i": index})
        received = [m async for m in backend.consume(batch_size=2, block_ms=0)]
        assert [m.payload["i"] for m in received] == [0, 1]
        assert len(list(_incoming(tmp_path, "batch").iterdir())) == 3
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_zero_batch_size_yields_nothing_and_claims_nothing(tmp_path: Path) -> None:
    backend = FileQueueBackend(tmp_path, topic="zero")
    try:
        await backend.publish({"i": 0})
        assert [m async for m in backend.consume(batch_size=0, block_ms=0)] == []
        assert len(list(_incoming(tmp_path, "zero").iterdir())) == 1
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_consumer_stopping_early_leaves_unclaimed_entries_in_incoming(tmp_path: Path) -> None:
    backend = FileQueueBackend(tmp_path, topic="early")
    try:
        for index in range(4):
            await backend.publish({"i": index})
        async with contextlib.aclosing(backend.consume(batch_size=4, block_ms=0)) as stream:
            first = await stream.__anext__()
        assert first.payload["i"] == 0
        assert len(list(_incoming(tmp_path, "early").iterdir())) == 3
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_quarantined_entries_do_not_count_toward_batch_size(tmp_path: Path) -> None:
    backend = FileQueueBackend(tmp_path, topic="quarantine")
    try:
        incoming = _incoming(tmp_path, "quarantine")
        (incoming / f"{0:020d}-bad.json").write_text("not json", encoding="utf-8")
        (incoming / f"{1:020d}-bad.json").write_text("not json", encoding="utf-8")
        await backend.publish({"i": 1})
        await backend.publish({"i": 2})
        await backend.publish({"i": 3})
        received = [m async for m in backend.consume(batch_size=2, block_ms=0)]
        assert [m.payload["i"] for m in received] == [1, 2]
        assert len(list((tmp_path / "quarantine" / "dead-letter").iterdir())) == 2
        assert len(list(incoming.iterdir())) == 1
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_empty_queue_with_positive_block_waits_out_the_window(tmp_path: Path) -> None:
    backend = FileQueueBackend(tmp_path, topic="idle")
    try:
        started = time.monotonic()
        received = [m async for m in backend.consume(batch_size=1, block_ms=150)]
        elapsed = time.monotonic() - started
        assert received == []
        assert 0.1 <= elapsed < 5
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_unbounded_block_waits_for_a_late_publish(tmp_path: Path) -> None:
    backend = FileQueueBackend(tmp_path, topic="late")
    try:

        async def publish_later() -> str:
            await asyncio.sleep(0.15)
            return await backend.publish({"late": True})

        publisher = asyncio.create_task(publish_later())
        received = await asyncio.wait_for(
            _first_of(backend.consume(batch_size=1, block_ms=None)), timeout=5
        )
        assert received.id == await publisher
    finally:
        await backend.close()


async def _first_of(stream):
    async with contextlib.aclosing(stream) as items:
        async for item in items:
            return item
    raise AssertionError("consume yielded nothing")


@pytest.mark.asyncio
async def test_lost_claim_with_zero_block_returns_without_polling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = FileQueueBackend(tmp_path, topic="lost")
    try:
        await backend.publish({"i": 0})
        monkeypatch.setattr(backend, "_claim", lambda name, state: False)
        received = await asyncio.wait_for(
            _collect(backend.consume(batch_size=1, block_ms=0)), timeout=2
        )
        assert received == []
    finally:
        await backend.close()


async def _collect(stream):
    return [item async for item in stream]


@pytest.mark.asyncio
async def test_consume_after_close_raises_queue_error(tmp_path: Path) -> None:
    backend = FileQueueBackend(tmp_path, topic="closed")
    await backend.close()
    with pytest.raises(QueueError):
        await _collect(backend.consume(batch_size=1, block_ms=0))


@pytest.mark.asyncio
async def test_expired_window_stops_at_the_first_quarantined_entry_without_sleeping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticks = iter([0.0] + [1.0] * 100)
    backend = FileQueueBackend(tmp_path, topic="expired", clock=lambda: next(ticks))
    try:
        incoming = _incoming(tmp_path, "expired")
        (incoming / f"{0:020d}-bad.json").write_text("not json", encoding="utf-8")
        (incoming / f"{1:020d}-bad.json").write_text("not json", encoding="utf-8")
        sleeps: list[float] = []

        async def record_sleep(delay: float, *args: object, **kwargs: object) -> None:
            sleeps.append(delay)

        monkeypatch.setattr(asyncio, "sleep", record_sleep)
        received = await asyncio.wait_for(
            _collect(backend.consume(batch_size=1, block_ms=100)), timeout=2
        )
        assert received == []
        assert sleeps == []
        assert len(list((tmp_path / "expired" / "dead-letter").iterdir())) == 1
        assert len(list(incoming.iterdir())) == 1
    finally:
        await backend.close()
