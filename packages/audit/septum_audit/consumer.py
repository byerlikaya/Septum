"""Audit consumer that ingests events from a ``septum-queue`` topic.

A payload that is not an object, or cannot be parsed into a record at all, is
acknowledged and logged as malformed; nothing is written. A record the sink refuses as
unstorable is kept as an ``UnstorablePlaceholder`` carrying its id, source, event type and
correlation id, with its content dropped. The message is acknowledged only after the
placeholder is written. Every other failure requeues the message, with backoff.

The consumer keeps a heartbeat sidecar next to the ledger (see ``consumer_status``).
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING

from .consumer_status import ConsumerStatus, write_consumer_status
from .events import AuditRecord, UnstorablePlaceholder, UnstorableRecordError, short_id
from .sink import AuditSink

if TYPE_CHECKING:
    from septum_queue import Message, QueueBackend

logger = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 60
HEARTBEAT_INTERVAL_SECONDS = 10.0
_BACKOFF_EXPONENT_CAP = math.ceil(math.log2(MAX_BACKOFF_SECONDS))
_ID_LIMIT = 64
_UNSTORABLE_MARKER = "<unstorable>"


def backoff_seconds(consecutive_failures: int) -> float:
    """Seconds to wait before the next consume: 2 ** n, capped at ``MAX_BACKOFF_SECONDS``."""
    return min(2 ** min(consecutive_failures, _BACKOFF_EXPONENT_CAP), MAX_BACKOFF_SECONDS)


class AuditConsumer:
    """Drive a single sink off a single audit queue topic."""

    def __init__(
        self,
        *,
        queue: "QueueBackend",
        sink: AuditSink,
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._queue = queue
        self._sink = sink
        self._sleep = sleep
        self._clock = clock
        # Only the JSONL sink has a ledger path; a sink without one (the memory sink) writes no heartbeat.
        self._status_path = getattr(sink, "path", None)
        self._failures = 0
        self._first_failure_at: float | None = None
        self._last_ingest_at: float | None = None
        self._last_published = -math.inf

    async def run_once(self, *, block_ms: int = 1000) -> bool:
        """Process at most one message. Return ``True`` when work happened."""
        async for message in self._queue.consume(batch_size=1, block_ms=block_ms):
            await self._handle(message)
            return True
        return False

    async def run_forever(self, *, block_ms: int = 5000) -> None:
        """Loop until cancelled. After a failed write, wait the backoff before the next consume."""
        while True:
            await self.run_once(block_ms=block_ms)
            if self._failures:
                await self._sleep(backoff_seconds(self._failures))

    async def _handle(self, message: "Message") -> None:
        try:
            record = _build_record(message.payload)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "audit consumer dropping malformed payload: id=%s error=%s",
                short_id(message.id),
                type(exc).__name__,
            )
            await self._queue.ack(message.id)
            return

        try:
            await self._sink.write(record)
        except UnstorableRecordError as exc:
            await self._write_placeholder(message, record, exc)
            return
        except Exception as exc:  # noqa: BLE001
            await self._requeue(message, record, exc)
            return
        await self._stored()
        await self._queue.ack(message.id)

    async def _write_placeholder(self, message: "Message", record: AuditRecord, cause: Exception) -> None:
        placeholder = UnstorablePlaceholder(
            id=record.id[:_ID_LIMIT],
            timestamp=math.nan,
            source=record.source,
            event_type=record.event_type,
            correlation_id=record.correlation_id,
            attributes={"value": _UNSTORABLE_MARKER},
        )
        try:
            await self._sink.write(placeholder)
        except Exception as exc:  # noqa: BLE001
            await self._requeue(message, record, exc)
            return
        logger.error(
            "audit consumer stored a placeholder for a record that cannot be stored: record_id=%s error=%s",
            short_id(record.id),
            type(cause).__name__,
        )
        await self._stored()
        await self._queue.ack(message.id)

    async def _requeue(self, message: "Message", record: AuditRecord, exc: Exception) -> None:
        now = self._clock()
        self._failures += 1
        if self._failures == 1:
            self._first_failure_at = now
        if self._failures == 1 or self._failures % 10 == 0:
            logger.error(
                "audit consumer sink write failed, will retry: consecutive_failures=%d record_id=%s error=%s",
                self._failures,
                short_id(record.id),
                type(exc).__name__,
            )
        await self._publish(now)
        await self._queue.nack(message.id, requeue=True)

    async def _stored(self) -> None:
        now = self._clock()
        self._last_ingest_at = now
        if self._failures:
            logger.info("audit consumer recovered after %d failed attempts", self._failures)
            self._failures = 0
            self._first_failure_at = None
            await self._publish(now)
        elif now - self._last_published >= HEARTBEAT_INTERVAL_SECONDS:
            await self._publish(now)

    async def _publish(self, now: float) -> None:
        self._last_published = now
        if self._status_path is None:
            return
        status = ConsumerStatus(
            last_ingest_at=self._last_ingest_at,
            consecutive_failures=self._failures,
            first_failure_at=self._first_failure_at,
            checked_at=now,
        )
        try:
            await asyncio.to_thread(write_consumer_status, self._status_path, status)
        except OSError as exc:
            logger.error("audit consumer status could not be written: error=%s", type(exc).__name__)


def _build_record(payload: object) -> AuditRecord:
    """Turn a producer payload into a record. Only an object is a record; the sink drops producer copies of its flags."""
    if not isinstance(payload, Mapping):
        raise TypeError("payload is not an object")
    data = dict(payload)
    data.pop("received_at", None)
    return AuditRecord.from_dict(data)
