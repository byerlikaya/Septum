"""Audit consumer that ingests events from a ``septum-queue`` topic."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .events import AuditRecord
from .sink import AuditSink

if TYPE_CHECKING:
    from septum_queue import Message, QueueBackend

logger = logging.getLogger(__name__)


class AuditConsumer:
    """Drive a single sink off a single audit queue topic."""

    def __init__(
        self,
        *,
        queue: "QueueBackend",
        sink: AuditSink,
    ) -> None:
        self._queue = queue
        self._sink = sink

    async def run_once(self, *, block_ms: int = 1000) -> bool:
        """Process at most one message. Return ``True`` when work happened."""
        async for message in self._queue.consume(batch_size=1, block_ms=block_ms):
            await self._handle(message)
            return True
        return False

    async def run_forever(self, *, block_ms: int = 5000) -> None:
        """Loop until cancelled, processing one message per iteration."""
        while True:
            await self.run_once(block_ms=block_ms)

    async def _handle(self, message: "Message") -> None:
        try:
            record = AuditRecord.from_dict(message.payload)
        except Exception as exc:  # noqa: BLE001
            # Poison pill: drop (ack) to unstick the loop, log for operator visibility.
            logger.error(
                "audit consumer dropping malformed payload: id=%s error=%s",
                message.id,
                exc,
            )
            await self._queue.ack(message.id)
            return

        try:
            await self._sink.write(record)
        except Exception:
            logger.exception(
                "audit consumer sink write failed: record_id=%s", record.id
            )
            await self._queue.nack(message.id, requeue=True)
            return

        await self._queue.ack(message.id)
