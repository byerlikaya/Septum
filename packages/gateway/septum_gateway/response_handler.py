"""Consumer loop pairing request envelopes with response envelopes.

Optional ``audit_queue`` hook emits a PII-free telemetry envelope
(provider / model / correlation_id / status / latency_ms / message_count)
per handled request — no prompt content, response text, or api keys.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from septum_queue import (
    Message,
    QueueBackend,
    RequestEnvelope,
    ResponseEnvelope,
)

from .forwarder import ForwarderRegistry, GatewayError, _sanitize_error_text

logger = logging.getLogger(__name__)


class GatewayConsumer:
    """Wire a request queue, a forwarder registry, and a response queue together."""

    def __init__(
        self,
        *,
        request_queue: QueueBackend,
        response_queue: QueueBackend,
        registry: ForwarderRegistry,
        audit_queue: QueueBackend | None = None,
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
        """Loop until cancelled, processing one message per iteration.

        ``await asyncio.sleep`` after empty cycles guarantees a yield
        even if the queue backend ignored ``block_ms`` (some file-
        backend variants return immediately on empty), so this loop
        cannot busy-spin and peg CPU on idle.
        """
        idle_sleep = max(0.05, min(block_ms / 1000, 1.0))
        while True:
            processed = await self.run_once(block_ms=block_ms)
            if not processed:
                await asyncio.sleep(idle_sleep)

    async def _handle(self, message: Message) -> None:
        try:
            envelope = RequestEnvelope.from_dict(message.payload)
        except Exception as exc:  # noqa: BLE001
            # No correlation id → ack-and-drop; a missing reply surfaces
            # via the producer-side timeout.
            logger.error("gateway dropping malformed request: %s", exc)
            await self._request_queue.ack(message.id)
            return

        started = time.monotonic()
        response = await self._forward(envelope)
        latency_ms = round((time.monotonic() - started) * 1000.0, 3)

        try:
            await self._response_queue.publish(response.to_dict())
        finally:
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
                error=_sanitize_error_text(str(exc)),
                provider=envelope.provider,
                model=envelope.model,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "gateway unexpected error: provider=%s correlation_id=%s",
                envelope.provider,
                envelope.correlation_id,
            )
            # Never echo the inner exception text — provider/transport
            # exceptions historically include header values (api keys,
            # bearer tokens) and request bodies.
            return ResponseEnvelope(
                correlation_id=envelope.correlation_id,
                error=f"unexpected gateway error ({type(exc).__name__})",
                provider=envelope.provider,
                model=envelope.model,
            )

    async def _emit_audit(
        self,
        envelope: RequestEnvelope,
        response: ResponseEnvelope,
        latency_ms: float,
    ) -> None:
        """Publish a PII-free audit envelope; swallow failures so the main path is never blocked."""
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
            # Stable reason code for the audit trail rather than the
            # full upstream error text; the response queue carries the
            # detailed message back to the producer for the user.
            error_lower = response.error.lower()
            if "rejected" in error_lower or "non-retryable" in error_lower:
                attributes["error"] = "provider_4xx"
            elif "after" in error_lower and "attempts" in error_lower:
                attributes["error"] = "provider_retries_exhausted"
            elif "disallowed" in error_lower or "allow-list" in error_lower:
                attributes["error"] = "blocked_base_url"
            else:
                attributes["error"] = "unknown"

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
