"""FileQueueBackend round-trip, persistence, and race-safety tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from septum_queue import FileQueueBackend, Message, QueueError, QueueSession


async def _consume_one(backend: FileQueueBackend, *, block_ms: int = 1000) -> Message:
    async for message in backend.consume(batch_size=1, block_ms=block_ms):
        return message
    raise AssertionError("backend.consume yielded nothing")


class TestFileQueueBackend:
    async def test_publish_then_consume_round_trip(self, tmp_path: Path):
        backend = FileQueueBackend(tmp_path, topic="llm-requests")
        try:
            mid = await backend.publish({"hello": "world"})
            assert mid.endswith(".json")
            message = await _consume_one(backend, block_ms=0)
            assert message.id == mid
            assert message.payload == {"hello": "world"}
            await backend.ack(message.id)
        finally:
            await backend.close()

    async def test_fifo_order_matches_publish_order(self, tmp_path: Path):
        backend = FileQueueBackend(tmp_path, topic="t")
        try:
            ids: list[str] = []
            for i in range(5):
                ids.append(await backend.publish({"i": i}))
            received: list[dict[str, Any]] = []
            async for message in backend.consume(batch_size=5, block_ms=0):
                received.append(dict(message.payload))
                await backend.ack(message.id)
            assert [item["i"] for item in received] == [0, 1, 2, 3, 4]
        finally:
            await backend.close()

    async def test_nack_requeue_returns_message_to_incoming(self, tmp_path: Path):
        backend = FileQueueBackend(tmp_path, topic="t")
        try:
            await backend.publish({"x": 1})
            first = await _consume_one(backend, block_ms=0)
            await backend.nack(first.id, requeue=True)
            second = await _consume_one(backend, block_ms=0)
            assert second.payload == {"x": 1}
        finally:
            await backend.close()

    async def test_nack_requeue_false_drops_message(self, tmp_path: Path):
        backend = FileQueueBackend(tmp_path, topic="t")
        try:
            await backend.publish({"x": 1})
            first = await _consume_one(backend, block_ms=0)
            await backend.nack(first.id, requeue=False)
            # Queue should be empty now; block_ms=0 returns immediately.
            got_any = False
            async for _ in backend.consume(batch_size=1, block_ms=0):
                got_any = True
            assert got_any is False
        finally:
            await backend.close()

    async def test_survives_backend_restart_with_pending_messages(self, tmp_path: Path):
        first = FileQueueBackend(tmp_path, topic="t")
        await first.publish({"persisted": True})
        await first.close()

        second = FileQueueBackend(tmp_path, topic="t")
        try:
            message = await _consume_one(second, block_ms=0)
            assert message.payload == {"persisted": True}
            await second.ack(message.id)
        finally:
            await second.close()

    async def test_two_consumers_race_only_one_wins_each_message(
        self, tmp_path: Path
    ):
        backend = FileQueueBackend(tmp_path, topic="t")
        try:
            total = 30
            for i in range(total):
                await backend.publish({"i": i})

            received_a: list[int] = []
            received_b: list[int] = []

            async def drain(target: list[int]) -> None:
                async for message in backend.consume(batch_size=total, block_ms=0):
                    target.append(int(message.payload["i"]))
                    await backend.ack(message.id)

            await asyncio.gather(drain(received_a), drain(received_b))

            combined = sorted(received_a + received_b)
            assert combined == list(range(total))
            # Race-guard: no duplicate should appear across the two consumers.
            assert len(set(combined)) == total
        finally:
            await backend.close()

    async def test_double_ack_is_idempotent(self, tmp_path: Path):
        backend = FileQueueBackend(tmp_path, topic="t")
        try:
            await backend.publish({"x": 1})
            message = await _consume_one(backend, block_ms=0)
            await backend.ack(message.id)
            # Second ack on the same id must not raise.
            await backend.ack(message.id)
        finally:
            await backend.close()

    async def test_publish_after_close_raises(self, tmp_path: Path):
        backend = FileQueueBackend(tmp_path, topic="t")
        await backend.close()
        with pytest.raises(QueueError):
            await backend.publish({"late": True})

    async def test_queue_session_closes_backend_on_exit(self, tmp_path: Path):
        backend = FileQueueBackend(tmp_path, topic="t")
        async with QueueSession(backend):
            await backend.publish({"inside": True})
        with pytest.raises(QueueError):
            await backend.publish({"after": True})

    async def test_partial_write_is_not_delivered(self, tmp_path: Path):
        """A .json.tmp file in incoming/ must not be visible to consumers."""
        backend = FileQueueBackend(tmp_path, topic="t")
        try:
            stray = tmp_path / "t" / "incoming" / "half-written.json.tmp"
            stray.write_text("{not", encoding="utf-8")
            got_any = False
            async for _ in backend.consume(batch_size=1, block_ms=0):
                got_any = True
            assert got_any is False
            assert stray.exists()  # tmp sibling left alone by the scanner
        finally:
            await backend.close()
