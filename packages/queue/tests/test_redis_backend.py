"""RedisStreamsQueueBackend round-trip tests using fakeredis.

Skipped automatically when neither ``redis`` nor ``fakeredis`` is
installed — the queue extra is optional and an air-gapped install
must not be forced to carry the Redis client just to run the test
suite.
"""

from __future__ import annotations

from pathlib import Path  # noqa: F401 — keeps pytest fixture autocompletion happy

import pytest

fakeredis = pytest.importorskip("fakeredis.aioredis")

from septum_queue import Message, RedisStreamsQueueBackend  # noqa: E402


@pytest.fixture()
async def backend():
    client = fakeredis.FakeRedis(decode_responses=False)
    backend = RedisStreamsQueueBackend(
        client,
        topic="llm-requests",
        group="gateway",
        consumer="consumer-a",
    )
    try:
        yield backend
    finally:
        await backend.close()


async def _drain_all(
    backend: RedisStreamsQueueBackend, *, batch_size: int = 10
) -> list[Message]:
    collected: list[Message] = []
    async for message in backend.consume(batch_size=batch_size, block_ms=0):
        collected.append(message)
    return collected


class TestRedisStreamsQueueBackend:
    async def test_publish_then_consume_round_trip(
        self, backend: RedisStreamsQueueBackend
    ):
        mid = await backend.publish({"hello": "world"})
        assert "-" in mid  # Redis stream ids look like "1700000000000-0"
        received = await _drain_all(backend, batch_size=1)
        assert len(received) == 1
        assert received[0].payload == {"hello": "world"}
        await backend.ack(received[0].id)

    async def test_consume_group_isolates_two_consumers(self):
        client = fakeredis.FakeRedis(decode_responses=False)
        a = RedisStreamsQueueBackend(
            client, topic="t", group="gateway", consumer="a"
        )
        b = RedisStreamsQueueBackend(
            client, topic="t", group="gateway", consumer="b"
        )
        try:
            total = 10
            for i in range(total):
                await a.publish({"i": i})
            received_a = await _drain_all(a, batch_size=total)
            received_b = await _drain_all(b, batch_size=total)
            combined = [int(m.payload["i"]) for m in received_a + received_b]
            assert sorted(combined) == list(range(total))
            # Group semantics: no entry is delivered to both consumers.
            assert len(set(combined)) == total
        finally:
            await a.close()
            await b.close()

    async def test_ack_marks_entry_acknowledged(
        self, backend: RedisStreamsQueueBackend
    ):
        await backend.publish({"x": 1})
        received = await _drain_all(backend, batch_size=1)
        await backend.ack(received[0].id)
        # After ack the same consumer must not see the entry again.
        re_read = await _drain_all(backend, batch_size=1)
        assert re_read == []

    async def test_nack_requeue_republishes_entry(
        self, backend: RedisStreamsQueueBackend
    ):
        await backend.publish({"x": 1})
        first = await _drain_all(backend, batch_size=1)
        await backend.nack(first[0].id, requeue=True)
        second = await _drain_all(backend, batch_size=1)
        assert second and second[0].payload == {"x": 1}
        # The requeued entry has a fresh id so the original is truly gone.
        assert second[0].id != first[0].id

    async def test_nack_drop_removes_entry(self, backend: RedisStreamsQueueBackend):
        await backend.publish({"x": 1})
        first = await _drain_all(backend, batch_size=1)
        await backend.nack(first[0].id, requeue=False)
        second = await _drain_all(backend, batch_size=1)
        assert second == []

    async def test_publish_creates_stream_on_cold_start(
        self, backend: RedisStreamsQueueBackend
    ):
        # The fixture has not touched the stream yet; the first publish
        # must implicitly call XGROUP CREATE ... MKSTREAM without raising.
        await backend.publish({"first": True})
        received = await _drain_all(backend, batch_size=1)
        assert received and received[0].payload == {"first": True}

    async def test_non_blocking_consume_returns_immediately_when_empty(
        self, backend: RedisStreamsQueueBackend
    ):
        received = await _drain_all(backend, batch_size=1)
        assert received == []
