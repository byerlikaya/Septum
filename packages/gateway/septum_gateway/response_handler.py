"""Consumer loop that pairs request envelopes with response envelopes.

The consumer reads from the request topic, dispatches each envelope to
the matching forwarder, and publishes a :class:`ResponseEnvelope` —
``text`` on success, ``error`` on any forwarder failure — keyed by the
same correlation id so the api producer can pair them up.

``run_forever`` blocks indefinitely. ``run_once`` processes at most one
message and returns whether work happened; tests and the FastAPI
``/process-once`` endpoint use the latter to drive the loop
deterministically without fighting a background task.
"""

from __future__ import annotations

import logging

from septum_queue import (
    Message,
    QueueBackend,
    RequestEnvelope,
    ResponseEnvelope,
)

from .forwarder import ForwarderRegistry, GatewayError

logger = logging.getLogger(__name__)


class GatewayConsumer:
    """Wire a request queue, a forwarder registry, and a response queue together."""

    def __init__(
        self,
        *,
        request_queue: QueueBackend,
        response_queue: QueueBackend,
        registry: ForwarderRegistry,
    ) -> None:
        self._request_queue = request_queue
        self._response_queue = response_queue
        self._registry = registry

    async def run_once(self, *, block_ms: int = 1000) -> bool:
        """Process at most one request. Return ``True`` when work happened."""
        async for message in self._request_queue.consume(
            batch_size=1, block_ms=block_ms
        ):
            await self._handle(message)
            return True
        return False

    async def run_forever(self, *, block_ms: int = 5000) -> None:
        """Loop until cancelled, processing one message per iteration."""
        while True:
            processed = await self.run_once(block_ms=block_ms)
            if not processed:
                continue  # idle cycle; block_ms already did the waiting

    async def _handle(self, message: Message) -> None:
        try:
            envelope = RequestEnvelope.from_dict(message.payload)
        except Exception as exc:  # noqa: BLE001
            # Malformed payload — ack so it does not loop, and do not
            # attempt a response (we have no correlation id).
            logger.error("gateway dropping malformed request: %s", exc)
            await self._request_queue.ack(message.id)
            return

        response = await self._forward(envelope)
        try:
            await self._response_queue.publish(response.to_dict())
        finally:
            # ACK even if the response publish failed — the request
            # itself completed; dropping the reply is surfaced via
            # the producer-side timeout instead of a redelivery storm.
            await self._request_queue.ack(message.id)

    async def _forward(self, envelope: RequestEnvelope) -> ResponseEnvelope:
        """Dispatch to the right forwarder and capture any failure as an error envelope."""
        try:
            forwarder = self._registry.for_provider(envelope.provider)
            text = await forwarder.complete(envelope)
            return ResponseEnvelope(
                correlation_id=envelope.correlation_id,
                text=text,
                provider=envelope.provider,
                model=envelope.model,
            )
        except GatewayError as exc:
            logger.warning(
                "gateway forwarder error: provider=%s correlation_id=%s error=%s",
                envelope.provider,
                envelope.correlation_id,
                exc,
            )
            return ResponseEnvelope(
                correlation_id=envelope.correlation_id,
                error=str(exc),
                provider=envelope.provider,
                model=envelope.model,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "gateway unexpected error: provider=%s correlation_id=%s",
                envelope.provider,
                envelope.correlation_id,
            )
            return ResponseEnvelope(
                correlation_id=envelope.correlation_id,
                error=f"unexpected gateway error: {type(exc).__name__}: {exc}",
                provider=envelope.provider,
                model=envelope.model,
            )
