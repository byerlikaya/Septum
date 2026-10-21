"""RedisStreamsQueueBackend round-trip tests using fakeredis.

Skipped automatically when neither ``redis`` nor ``fakeredis`` is
installed — the queue extra is optional and an air-gapped install
must not be forced to carry the Redis client just to run the test
suite.
"""

from __future__ import annotations

import json
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


class TestStreamRetention:
    """Acked, dropped and requeued entries leave the stream, not only the PEL."""

    async def test_ack_deletes_entry_from_stream(self, backend: RedisStreamsQueueBackend):
        await backend.publish({"x": 1})
        received = await _drain_all(backend, batch_size=1)
        await backend.ack(received[0].id)

        assert await backend._redis.xlen(backend._stream) == 0
        assert await backend._redis.xpending(backend._stream, "gateway") == {
            "pending": 0, "min": None, "max": None, "consumers": []
        }

    async def test_nack_drop_deletes_entry_from_stream(self, backend: RedisStreamsQueueBackend):
        await backend.publish({"x": 1})
        received = await _drain_all(backend, batch_size=1)
        await backend.nack(received[0].id, requeue=False)

        assert await backend._redis.xlen(backend._stream) == 0

    async def test_nack_requeue_leaves_only_the_copy(self, backend: RedisStreamsQueueBackend):
        await backend.publish({"x": 1})
        first = await _drain_all(backend, batch_size=1)
        await backend.nack(first[0].id, requeue=True)

        assert await backend._redis.xlen(backend._stream) == 1
        assert await backend._redis.xrange(backend._stream, min=first[0].id, max=first[0].id) == []
        second = await _drain_all(backend, batch_size=1)
        assert second[0].payload == {"x": 1}


class TestMalformedEntries:
    """Entries that cannot be decoded are acked, deleted and logged without payload."""

    @pytest.mark.parametrize(
        "fields",
        [
            {b"other": b"secret-sentinel"},
            {b"data": b"secret-sentinel{"},
            {b"data": b"\xffsecret-sentinel"},
            {b"data": b'["secret-sentinel"]'},
        ],
    )
    async def test_malformed_entry_is_dropped(
        self,
        backend: RedisStreamsQueueBackend,
        caplog: pytest.LogCaptureFixture,
        fields: dict,
    ):
        await backend.publish({"ok": True})
        await backend._redis.xadd(backend._stream, fields)

        received = await _drain_all(backend)

        assert [m.payload for m in received] == [{"ok": True}]
        assert await backend._redis.xlen(backend._stream) == 1
        pending = await backend._redis.xpending(backend._stream, "gateway")
        assert pending["pending"] == 1
        good_id = received[0].id.encode()
        assert pending["min"] == pending["max"] == good_id
        survivors = await backend._redis.xrange(backend._stream)
        assert [entry_id for entry_id, _ in survivors] == [good_id]
        assert json.loads(survivors[0][1][b"data"]) == {"ok": True}
        assert "Dropping malformed entry" in caplog.text
        assert "secret-sentinel" not in caplog.text


@pytest.fixture()
def redis_server_url():
    """Start a throwaway local ``redis-server``; skip when it is not installed."""
    import shutil
    import socket
    import subprocess
    import time

    binary = shutil.which("redis-server")
    if binary is None:
        pytest.skip("redis-server is not installed")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen(
        [binary, "--port", str(port), "--bind", "127.0.0.1", "--save", "", "--appendonly", "no"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with socket.socket() as client:
                if client.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.05)
        yield f"redis://127.0.0.1:{port}/0"
    finally:
        process.terminate()
        process.wait(timeout=5)


class TestBlockingReadAgainstSocketTimeout:
    """A blocking read longer than the socket timeout fails; shorter ones are safe.

    This is why callers cap ``block_ms`` below the client's socket timeout
    and loop until their own deadline.
    """

    async def test_block_below_socket_timeout_returns_empty(self, redis_server_url: str):
        backend = RedisStreamsQueueBackend.from_url(
            redis_server_url, topic="t", consumer="c", socket_timeout=0.5
        )
        try:
            received = [m async for m in backend.consume(block_ms=200)]
        finally:
            await backend.close()
        assert received == []

    async def test_block_above_socket_timeout_raises(self, redis_server_url: str):
        from redis.exceptions import TimeoutError as RedisTimeoutError

        backend = RedisStreamsQueueBackend.from_url(
            redis_server_url, topic="t", consumer="c", socket_timeout=0.5
        )
        try:
            with pytest.raises(RedisTimeoutError):
                [m async for m in backend.consume(block_ms=1500)]
        finally:
            await backend.close()
