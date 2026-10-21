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

import json
import logging
from typing import TYPE_CHECKING, AsyncIterator, Mapping

from .base import QueueBackend, QueueError
from .models import Message

logger = logging.getLogger(__name__)

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
        socket_timeout: float = 30.0,
        socket_connect_timeout: float = 10.0,
        health_check_interval: float = 30.0,
        ssl_cert_reqs: str = "required",
    ) -> "RedisStreamsQueueBackend":
        """Construct from a ``redis://`` or ``rediss://`` URL.

        Ownership of the client is transferred. ``rediss://`` schemes
        are TLS-verified by default (ssl_cert_reqs=required +
        ssl_check_hostname=True). Plain ``redis://`` over a routable
        network ships envelopes — including any operational metadata —
        in cleartext; restrict to loopback or a private link.

        Other schemes (or no scheme) are rejected outright so a typo
        in ``SEPTUM_QUEUE_URL`` cannot fall through to an unintended
        transport.
        """
        scheme = (url.split("://", 1)[0] if "://" in url else "").lower()
        if scheme not in {"redis", "rediss", "unix"}:
            raise QueueError(
                f"unsupported queue URL scheme {scheme!r}; expected "
                "'redis://', 'rediss://', or 'unix://'."
            )
        Redis = _load_redis_async()
        kwargs: dict[str, object] = {
            "decode_responses": False,
            "socket_timeout": socket_timeout,
            "socket_connect_timeout": socket_connect_timeout,
            "health_check_interval": health_check_interval,
        }
        if scheme == "rediss":
            kwargs["ssl_cert_reqs"] = ssl_cert_reqs
            kwargs["ssl_check_hostname"] = True
        client = Redis.from_url(url, **kwargs)
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

    async def _reclaim_idle_pending(
        self, *, min_idle_ms: int, batch_size: int
    ) -> list[tuple[str, Mapping]]:
        """Pull pending entries idle longer than ``min_idle_ms`` from any consumer.

        Implements the at-least-once recovery promise in the module
        docstring: if a consumer crashes between delivery and ack, the
        entry sits in PEL forever unless someone XAUTOCLAIMs it. We
        run this sweep before each XREADGROUP `>` so a crashed
        gateway's in-flight requests are eventually re-delivered to
        the next live consumer.
        """
        try:
            response = await self._redis.xautoclaim(
                name=self._stream,
                groupname=self._group,
                consumername=self._consumer,
                min_idle_time=min_idle_ms,
                start_id="0-0",
                count=max(1, batch_size),
            )
        except Exception:  # noqa: BLE001
            # XAUTOCLAIM was added in Redis 6.2; on older deployments
            # we degrade silently — the rest of the consumer path keeps
            # working, callers just lose orphan reclamation.
            return []
        if not response:
            return []
        # redis-py returns either ``(next_id, claimed_entries, deleted_ids)``
        # (Redis 7.0+) or ``(next_id, claimed_entries)`` (6.2). Treat
        # both shapes the same.
        claimed = response[1] if len(response) >= 2 else []
        return list(claimed)

    async def consume(
        self,
        *,
        batch_size: int = 1,
        block_ms: int | None = None,
        reclaim_min_idle_ms: int = 60_000,
    ) -> AsyncIterator[Message]:
        """XREADGROUP up to ``batch_size`` entries, blocking for ``block_ms`` ms.

        ``block_ms=None`` is treated as "wait forever". ``block_ms=0``
        means "return immediately if nothing is ready" — XREADGROUP
        uses 0 for non-blocking, matching our contract.

        Before each batch we sweep PEL for entries idle longer than
        ``reclaim_min_idle_ms`` (default 60s) and yield those first so
        a crashed consumer's in-flight requests do not stick around
        consuming Redis memory forever.
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

        # First: reclaim orphans (cheap, never blocks). Yield them as
        # ordinary messages — the consumer ack/nack path handles them
        # the same way as fresh deliveries.
        reclaimed = await self._reclaim_idle_pending(
            min_idle_ms=reclaim_min_idle_ms, batch_size=batch_size
        )
        for message in await self._decode_entries(reclaimed):
            yield message

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
        fresh = [entry for _stream, entries in response for entry in entries]
        for message in await self._decode_entries(fresh):
            yield message

    async def _decode_entries(self, entries) -> list[Message]:
        """Decode stream entries, acking and deleting the malformed ones.

        An entry without a ``data`` field, or whose ``data`` is not a UTF-8
        JSON object, would otherwise sit in the PEL and be reclaimed forever.
        The drop is logged by entry id and reason only, never the payload.
        """
        messages: list[Message] = []
        malformed: list[str] = []
        for entry_id, fields in entries:
            raw_id = entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)
            payload, reason = _decode_payload(fields)
            if payload is None:
                logger.warning(
                    "Dropping malformed entry %s from %s: %s", raw_id, self._stream, reason
                )
                malformed.append(raw_id)
                continue
            messages.append(Message(id=raw_id, payload=payload))
        if malformed:
            await self._ack_and_delete(*malformed)
        return messages

    async def _ack_and_delete(self, *message_ids: str) -> None:
        """XACK and XDEL ``message_ids`` in one transaction.

        Every topic stream has exactly one consumer group, so an entry acked
        by that group is read by nobody else; deleting it keeps the stream
        from growing without bound and keeps masked payloads from lingering.
        """
        pipe = self._redis.pipeline(transaction=True)
        pipe.xack(self._stream, self._group, *message_ids)
        pipe.xdel(self._stream, *message_ids)
        await pipe.execute()

    async def ack(self, message_id: str) -> None:
        """Acknowledge ``message_id`` and delete it from the stream."""
        self._ensure_open()
        await self._ack_and_delete(message_id)

    async def nack(self, message_id: str, *, requeue: bool = True) -> None:
        """Redis Streams has no native nack — simulate via ack + re-XADD.

        ``requeue=False`` XACKs and XDELs the entry so it leaves both the
        pending entries list and the stream. ``requeue=True`` XRANGEs the
        entry back to recover its payload, then in one transaction XADDs a
        fresh copy (new id) and XACKs + XDELs the original.

        If the original entry has vanished (deleted, or already requeued
        by another consumer), the XACK is *not* issued so the entry stays
        in PEL — XAUTOCLAIM in the next consume cycle will recover it.
        Silently dropping the message in that case used to lose payloads;
        raise ``QueueError`` so the caller knows something is off.
        """
        self._ensure_open()
        if not requeue:
            await self._ack_and_delete(message_id)
            return

        response = await self._redis.xrange(self._stream, min=message_id, max=message_id)
        if not response:
            raise QueueError(
                f"cannot requeue {message_id!r}: entry vanished from stream"
            )
        _entry_id, fields = response[0]
        pipe = self._redis.pipeline(transaction=True)
        pipe.xadd(self._stream, fields)
        pipe.xack(self._stream, self._group, message_id)
        pipe.xdel(self._stream, message_id)
        await pipe.execute()

    async def close(self) -> None:
        """Close the underlying Redis connection pool."""
        if self._closed:
            return
        self._closed = True
        try:
            await self._redis.aclose()
        except AttributeError:  # pragma: no cover — redis-py < 5.0 shim
            await self._redis.close()


def _decode_payload(fields) -> tuple[dict | None, str]:
    """Return ``(payload, "")`` for a well-formed entry, else ``(None, reason)``."""
    data = (fields.get(b"data") or fields.get("data")) if hasattr(fields, "get") else None
    if data is None:
        return None, "missing data field"
    try:
        text = data.decode("utf-8") if isinstance(data, bytes) else str(data)
        payload = json.loads(text)
    except (UnicodeDecodeError, ValueError):
        return None, "data is not UTF-8 JSON"
    if not isinstance(payload, dict):
        return None, "data is not a JSON object"
    return payload, ""
