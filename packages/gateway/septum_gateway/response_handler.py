"""Consumer loop that pairs request envelopes with response envelopes.

The consumer reads from the request topic, dispatches each envelope to
the matching forwarder, and publishes a :class:`ResponseEnvelope` —
``text`` on success, ``error`` on any forwarder failure — keyed by the
same correlation id so the api producer can pair them up.

``run_forever`` blocks indefinitely. ``run_once`` processes at most one
message and returns whether work happened; tests and the FastAPI
``/process-once`` endpoint use the latter to drive the loop
deterministically without fighting a background task.

Optional audit hook: when the consumer is constructed with an
``audit_queue``, every handled message also produces a small audit
envelope (provider / model / correlation_id / status / latency_ms /
error / message_count) on the configured topic. The envelope contains
**no** prompt content, response text, api keys, or base URLs — only
metadata an internet-facing observer is allowed to see — so the gateway
can stream telemetry into ``septum-audit`` without breaking the
no-PII-leaves-the-zone invariant.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Optional

from septum_queue import (
    Message,
    QueueBackend,
    RequestEnvelope,
    ResponseEnvelope,
)

from .forwarder import ForwarderRegistry, GatewayError

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class GatewayConsumer:
    """Wire a request queue, a forwarder registry, and a response queue together."""

    def __init__(
        self,
        *,
        request_queue: QueueBackend,
        response_queue: QueueBackend,
        registry: ForwarderRegistry,
        audit_queue: Optional[QueueBackend] = None,
    ) -> None:
        self._request_queue = request_queue
        self._response_queue = response_queue
        self._registry = registry
        self._audit_queue = audit_queue

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

        started = time.monotonic()
        response = await self._forward(envelope)
        latency_ms = round((time.monotonic() - started) * 1000.0, 3)

        try:
            await self._response_queue.publish(response.to_dict())
        finally:
            # ACK even if the response publish failed — the request
            # itself completed; dropping the reply is surfaced via
            # the producer-side timeout instead of a redelivery storm.
            await self._request_queue.ack(message.id)

        await self._emit_audit(envelope, response, latency_ms)

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

    async def _emit_audit(
        self,
        envelope: RequestEnvelope,
        response: ResponseEnvelope,
        latency_ms: float,
    ) -> None:
        """Publish a PII-free audit envelope when an audit queue is wired in.

        Failures here are swallowed (logged but not raised) so the
        primary request/response path is not blocked by an audit-side
        outage. Operator visibility comes from the warning log, not from
        a propagated exception.
        """
        if self._audit_queue is None:
            return
        status = "ok" if response.error is None else "error"
        attributes: dict[str, object] = {
            "provider": envelope.provider,
            "model": envelope.model,
            "status": status,
            "latency_ms": latency_ms,
            "message_count": len(envelope.messages),
        }
        if envelope.max_tokens is not None:
            attributes["max_tokens"] = envelope.max_tokens
        if response.error is not None:
            attributes["error"] = response.error

        payload = {
            "id": uuid.uuid4().hex,
            "timestamp": time.time(),
            "source": "septum-gateway",
            "event_type": (
                "llm.request.completed" if status == "ok" else "llm.request.failed"
            ),
            "correlation_id": envelope.correlation_id,
            "attributes": attributes,
        }
        try:
            await self._audit_queue.publish(payload)
        except Exception:
            logger.warning(
                "gateway audit publish failed: correlation_id=%s",
                envelope.correlation_id,
                exc_info=True,
            )
