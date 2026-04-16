"""Audit consumer that ingests events from a ``septum-queue`` topic.

The producer side (``septum-gateway``, ``septum-api``) publishes audit
records as plain dicts to a dedicated topic. This consumer reads them
off the queue, rebuilds the :class:`AuditRecord`, persists into the
configured sink, then acks. Mirrors the ``GatewayConsumer`` shape so an
operator running both processes sees the same ``run_once`` /
``run_forever`` surface.

The dependency on ``septum-queue`` is gated behind the ``[queue]`` extra
in ``pyproject.toml``; importing this module without the extra installed
raises a clear ``ImportError`` rather than failing later in a queue
iteration deep inside the loop.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .events import AuditRecord
from .sink import AuditSink

if TYPE_CHECKING:
    from septum_queue import QueueBackend

logger = logging.getLogger(__name__)


def _import_queue_backend() -> type:
    try:
        from septum_queue import QueueBackend  # noqa: WPS433 (intentional lazy)
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "septum-audit[queue] is required to use AuditConsumer. "
            "Install with: pip install 'septum-audit[queue]'"
        ) from exc
    return QueueBackend


class AuditConsumer:
    """Drive a single sink off a single audit queue topic."""

    def __init__(
        self,
        *,
        queue: "QueueBackend",
        sink: AuditSink,
    ) -> None:
        # Force the queue import at construction time so a misconfigured
        # deployment fails fast at startup, not on the first message.
        _import_queue_backend()
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

    async def _handle(self, message) -> None:
        try:
            record = AuditRecord.from_dict(message.payload)
        except Exception as exc:  # noqa: BLE001
            # Malformed payloads are dropped (acked) rather than looped —
            # a poison pill should not stall the audit pipeline. The
            # error is logged so operators can investigate via ordinary
            # log search.
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
            # Sink failures should not silently drop the record — nack
            # so it can be retried by another consumer or after a sink
            # recovers.
            logger.exception(
                "audit consumer sink write failed: record_id=%s", record.id
            )
            await self._queue.nack(message.id, requeue=True)
            return

        await self._queue.ack(message.id)
