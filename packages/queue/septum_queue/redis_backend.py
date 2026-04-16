"""Redis Streams backend for shared-infrastructure Septum deployments.

Uses XADD / XREADGROUP / XACK primitives so multiple gateway instances
can consume from a single stream with at-least-once semantics. Each
topic maps to one stream (``septum:{topic}``); each consumer joins a
named group (``gateway`` by default) and pulls its own share. If a
consumer dies mid-processing the stream remembers the delivery and
another consumer picks it up after the idle threshold.

Why Redis Streams instead of pub/sub:

* Pub/sub is fire-and-forget — a consumer offline during a publish
  loses that message. Streams persist.
* Streams give you consumer groups out of the box. No need to re-
  invent load-balancing or add-on modules.
* XACK makes the ack / nack semantics map cleanly onto our
  :class:`QueueBackend` contract.

Payloads are stored as a single ``data`` field holding JSON text, not
as a fanned-out hash. Fanning-out would force callers to flatten nested
payloads into string fields and then reconstruct types on the consumer
side; keeping it as a JSON blob means the same codec works for every
envelope shape.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, AsyncIterator, Mapping

from .base import QueueBackend, QueueError
from .models import Message

if TYPE_CHECKING:
    from redis.asyncio import Redis


def _load_redis_async() -> "type[Redis]":
    """Import ``redis.asyncio.Redis`` lazily so the base package stays zero-dep.

    Installing ``septum-queue[redis]`` pulls the client in; a bare
    ``pip install septum-queue`` does not.
    """
    try:
        from redis.asyncio import Redis
    except ImportError as exc:  # pragma: no cover
        raise QueueError(
            "RedisStreamsQueueBackend requires the [redis] extra. "
            "Install with: pip install 'septum-queue[redis]'"
        ) from exc
    return Redis


class RedisStreamsQueueBackend(QueueBackend):
    """Consumer-group backed queue for multi-consumer deployments."""

    topic: str

    def __init__(
        self,
        redis_client: "Redis",
        *,
        topic: str,
        group: str = "gateway",
        consumer: str | None = None,
        stream_prefix: str = "septum:",
    ) -> None:
        self.topic = topic
        self._redis = redis_client
        self._group = group
        self._consumer = consumer or f"consumer-{id(self):x}"
        self._stream = f"{stream_prefix}{topic}"
        self._group_ready = False
        self._closed = False

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        topic: str,
        group: str = "gateway",
        consumer: str | None = None,
        stream_prefix: str = "septum:",
    ) -> "RedisStreamsQueueBackend":
        """Construct from a ``redis://`` URL. Ownership of the client is transferred."""
        Redis = _load_redis_async()
        client = Redis.from_url(url, decode_responses=False)
        return cls(
            client,
            topic=topic,
            group=group,
            consumer=consumer,
            stream_prefix=stream_prefix,
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise QueueError("RedisStreamsQueueBackend has been closed")

    async def _ensure_group(self) -> None:
        """Create the consumer group on first use.

        ``XGROUP CREATE ... MKSTREAM`` is idempotent-ish: it raises
        ``BUSYGROUP`` if the group already exists, which we silently
        swallow so racing consumers can both bring a cold stream up.
        """
        if self._group_ready:
            return
        try:
            await self._redis.xgroup_create(
                name=self._stream,
                groupname=self._group,
                id="0",
                mkstream=True,
            )
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            if "BUSYGROUP" not in message:
                raise QueueError(f"failed to create consumer group: {exc}") from exc
        self._group_ready = True

    async def publish(self, payload: Mapping[str, object]) -> str:
        """XADD a JSON-encoded entry onto the topic stream."""
        self._ensure_open()
        await self._ensure_group()
        encoded = json.dumps(dict(payload), separators=(",", ":"))
        entry_id = await self._redis.xadd(
            self._stream,
            {b"data": encoded.encode("utf-8")},
        )
        return entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)

    async def consume(
        self,
        *,
        batch_size: int = 1,
        block_ms: int | None = None,
    ) -> AsyncIterator[Message]:
        """XREADGROUP up to ``batch_size`` entries, blocking for ``block_ms`` ms.

        ``block_ms=None`` is treated as "wait forever". ``block_ms=0``
        means "return immediately if nothing is ready" — XREADGROUP
        uses 0 for non-blocking, matching our contract.
        """
        self._ensure_open()
        await self._ensure_group()

        # Redis treats block=0 as "block indefinitely" and omits block
        # entirely for non-blocking. Normalize our contract to theirs.
        if block_ms is None:
            redis_block = 0
            non_blocking = False
        elif block_ms <= 0:
            redis_block = None
            non_blocking = True
        else:
            redis_block = block_ms
            non_blocking = False

        kwargs: dict[str, object] = {
            "groupname": self._group,
            "consumername": self._consumer,
            "streams": {self._stream: ">"},
            "count": max(1, batch_size),
        }
        if not non_blocking:
            kwargs["block"] = redis_block

        response = await self._redis.xreadgroup(**kwargs)
        if not response:
            return
        # xreadgroup returns [(stream_name, [(entry_id, {field: value})])].
        for _stream, entries in response:
            for entry_id, fields in entries:
                raw_id = entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)
                data = fields.get(b"data") or fields.get("data")
                if data is None:
                    # Ack and skip corrupted entries so they do not wedge
                    # the consumer group forever.
                    await self._redis.xack(self._stream, self._group, raw_id)
                    continue
                text = data.decode() if isinstance(data, bytes) else str(data)
                payload = json.loads(text)
                yield Message(id=raw_id, payload=payload)

    async def ack(self, message_id: str) -> None:
        self._ensure_open()
        await self._redis.xack(self._stream, self._group, message_id)

    async def nack(self, message_id: str, *, requeue: bool = True) -> None:
        """Redis Streams has no native nack — simulate via ack + re-XADD.

        ``requeue=False`` simply XACKs the entry so it is dropped from
        the pending entries list. ``requeue=True`` XREADs the entry
        back to recover its payload, re-XADDs a fresh copy (new id),
        then XACKs the original so it is not re-delivered to the
        dead-letter visibility timeout.
        """
        self._ensure_open()
        if not requeue:
            await self._redis.xack(self._stream, self._group, message_id)
            return

        response = await self._redis.xrange(self._stream, min=message_id, max=message_id)
        if response:
            _entry_id, fields = response[0]
            await self._redis.xadd(self._stream, fields)
        await self._redis.xack(self._stream, self._group, message_id)

    async def close(self) -> None:
        """Close the underlying Redis connection pool."""
        if self._closed:
            return
        self._closed = True
        try:
            await self._redis.aclose()
        except AttributeError:  # pragma: no cover — redis-py < 5.0 shim
            await self._redis.close()
