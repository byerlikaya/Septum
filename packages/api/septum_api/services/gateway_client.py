"""Producer-side client for the septum-gateway RPC flow.

When ``AppSettings.use_gateway`` is ``True`` the api does NOT call cloud
LLM APIs directly. Instead it publishes a :class:`RequestEnvelope` onto
the request topic and waits for the matching :class:`ResponseEnvelope`
on the reply topic, paired by ``correlation_id``. Everything between
those two calls runs inside the gateway zone — the api keeps zero
egress to cloud LLM endpoints, which is the whole point of the split
deployment.

Gateway delivery semantics:

* At-least-once. The consumer acks after publishing a reply so a
  crash mid-process results in the original being redelivered to
  another consumer. The producer side dedupes via ``correlation_id``.
* Timeout-bound. The producer does not wait forever; a missing reply
  after ``timeout_seconds`` raises :class:`QueueTimeoutError` so the
  caller can fall back to Ollama just like it does today when the
  direct cloud call fails.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Sequence

from septum_queue import (
    QueueBackend,
    QueueTimeoutError,
    RequestEnvelope,
    ResponseEnvelope,
)

from ..models.settings import AppSettings
from .llm_errors import LLMRouterError

logger = logging.getLogger(__name__)


ChatMessage = dict[str, str]


# Maps the AppSettings.llm_provider string to the field that holds the
# matching cloud API key on the same row. Lookup misses (e.g. "ollama")
# yield a ``None`` api_key, which is correct: Ollama runs local and
# never crosses the queue.
_PROVIDER_API_KEY_FIELD: dict[str, str] = {
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
    "openrouter": "openrouter_api_key",
}


class GatewayClient:
    """Wraps a queue producer + matching consumer for gateway RPC calls.

    One ``GatewayClient`` owns two backends: a request topic it
    publishes onto, and a response topic it consumes. The constructor
    does not mutate the backends so the caller keeps ownership and
    decides when to close them.
    """

    def __init__(
        self,
        *,
        request_queue: QueueBackend,
        response_queue: QueueBackend,
        timeout_seconds: float = 60.0,
        poll_batch_size: int = 8,
    ) -> None:
        self._request_queue = request_queue
        self._response_queue = response_queue
        self._timeout_seconds = timeout_seconds
        self._poll_batch_size = poll_batch_size

    async def complete(
        self,
        *,
        settings: AppSettings,
        messages: Sequence[ChatMessage],
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        """Publish a request envelope and wait for its response.

        Returns the masked answer text on success. Any gateway-side
        failure (missing api key, upstream 5xx, unsupported provider)
        arrives as a :class:`ResponseEnvelope` with ``error`` set;
        we re-raise as :class:`LLMRouterError` so the existing
        ``LLMRouter`` fallback path treats it identically to a direct
        cloud call failure.
        """
        envelope = self._build_envelope(
            settings, list(messages), temperature, max_tokens
        )
        await self._request_queue.publish(envelope.to_dict())

        response = await self._await_response(envelope.correlation_id)
        if response.error is not None:
            raise LLMRouterError(f"gateway: {response.error}")
        if response.text is None:
            raise LLMRouterError(
                "gateway returned an envelope with neither text nor error"
            )
        return response.text

    def _build_envelope(
        self,
        settings: AppSettings,
        messages: list[ChatMessage],
        temperature: float,
        max_tokens: int | None,
    ) -> RequestEnvelope:
        """Shape a RequestEnvelope from the current AppSettings row.

        Cloud-provider credentials are intentionally NOT placed on the
        envelope: the gateway owns its own keys via env-loaded
        ``GatewayConfig``. Producer-side keys (``settings.*_api_key``)
        are now used only by the same-process Ollama / direct paths.
        """
        provider = (settings.llm_provider or "").strip().lower()
        return RequestEnvelope.new(
            provider=provider,
            model=settings.llm_model,
            messages=list(messages),
            temperature=float(temperature),
            max_tokens=max_tokens,
        )

    async def _await_response(self, correlation_id: str) -> ResponseEnvelope:
        """Consume the response topic until we see our correlation id.

        Replies for other in-flight requests are silently re-queued so
        another waiter's coroutine can pick them up. At Septum's
        single-user scale the occasional re-queue costs nothing; a
        local fan-out router is a future optimization.
        """
        deadline = time.monotonic() + self._timeout_seconds
        while True:
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                break
            async for message in self._response_queue.consume(
                batch_size=self._poll_batch_size,
                block_ms=remaining_ms,
            ):
                envelope = ResponseEnvelope.from_dict(message.payload)
                if envelope.correlation_id == correlation_id:
                    await self._response_queue.ack(message.id)
                    return envelope
                await self._response_queue.nack(message.id, requeue=True)
                await asyncio.sleep(0.01)

        raise QueueTimeoutError(
            f"gateway did not reply within {self._timeout_seconds}s "
            f"(correlation_id={correlation_id})"
        )
