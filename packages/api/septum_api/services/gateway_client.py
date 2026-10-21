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
  direct cloud call fails. The default covers the gateway's worst case
  (``septum_gateway.forwarder`` module docstring: three 30 s attempt
  deadlines, two Retry-After waits capped at 10 s, about 110 s) so a slow
  but healthy reply is not abandoned. Keep this timeout above that bound.
* Orphan replies. A reply whose waiter already gave up would otherwise
  be requeued forever; once it is older than twice the timeout it is
  acked and dropped.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from typing import Sequence

from septum_queue import (
    LLM_REQUEST_TOPIC,
    LLM_RESPONSE_TOPIC,
    QueueBackend,
    QueueError,
    QueueTimeoutError,
    RequestEnvelope,
    ResponseEnvelope,
    backend_from_env,
)

from ..models.settings import AppSettings
from .llm_errors import LLM_UNAVAILABLE, GatewayStartupError, LLMRouterError

logger = logging.getLogger(__name__)

REQUEST_TOPIC_ENV = "SEPTUM_GATEWAY_REQUEST_TOPIC"
RESPONSE_TOPIC_ENV = "SEPTUM_GATEWAY_RESPONSE_TOPIC"
CLIENT_TIMEOUT_ENV = "SEPTUM_GATEWAY_CLIENT_TIMEOUT_SECONDS"
DEFAULT_CLIENT_TIMEOUT_SECONDS = 120.0
ORPHAN_AGE_TIMEOUT_MULTIPLIER = 2
# One blocking read must end before the Redis client's socket timeout
# (``RedisStreamsQueueBackend.from_url``: 30 s), or the read itself fails;
# the wait loop re-issues reads until the client deadline.
MAX_BLOCK_MS = 25_000
GATEWAY_FAILED_MESSAGE = "The gateway could not complete the language model call."
GATEWAY_INVALID_REPLY_MESSAGE = "The gateway returned an unusable reply."


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

    One ``GatewayClient`` uses two backends: a request topic it
    publishes onto, and a response topic it consumes. The constructor
    does not mutate the backends; whoever built them closes them, either
    directly or through :meth:`aclose` when the client was built by
    :func:`build_gateway_client_from_env`.
    """

    def __init__(
        self,
        *,
        request_queue: QueueBackend,
        response_queue: QueueBackend,
        timeout_seconds: float = DEFAULT_CLIENT_TIMEOUT_SECONDS,
        poll_batch_size: int = 8,
        max_block_ms: int = MAX_BLOCK_MS,
    ) -> None:
        self._request_queue = request_queue
        self._response_queue = response_queue
        self._timeout_seconds = timeout_seconds
        self._orphan_age_seconds = timeout_seconds * ORPHAN_AGE_TIMEOUT_MULTIPLIER
        self._poll_batch_size = poll_batch_size
        self._max_block_ms = max_block_ms

    async def aclose(self) -> None:
        """Close both backends; the response backend is closed even if the request one fails."""
        try:
            await self._request_queue.close()
        finally:
            await self._response_queue.close()

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
        cloud call failure. The gateway's error text comes from outside
        the air-gapped zone, so it is neither raised nor logged: the
        error carries a fixed code and message.
        """
        envelope = self._build_envelope(
            settings, list(messages), temperature, max_tokens
        )
        await self._request_queue.publish(envelope.to_dict())

        response = await self._await_response(envelope.correlation_id)
        if response.error is not None:
            logger.warning("Gateway reported a failed provider call")
            raise LLMRouterError(GATEWAY_FAILED_MESSAGE, code=LLM_UNAVAILABLE)
        if response.text is None:
            logger.warning("Gateway reply carried neither text nor error")
            raise LLMRouterError(GATEWAY_INVALID_REPLY_MESSAGE, code=LLM_UNAVAILABLE)
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

        Replies for other in-flight requests are re-queued so another
        waiter's coroutine can pick them up. A reply older than twice the
        timeout has no waiter left, so it is acked and dropped instead;
        only its age is logged, never its payload. A malformed reply
        (unparseable, or with a non-finite ``created_at``) is acked and
        dropped too. Each blocking read is capped at ``max_block_ms`` so it
        ends before the queue client's socket timeout.
        """
        deadline = time.monotonic() + self._timeout_seconds
        while True:
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                break
            async for message in self._response_queue.consume(
                batch_size=self._poll_batch_size,
                block_ms=min(remaining_ms, self._max_block_ms),
            ):
                envelope = _parse_reply(message.payload)
                if envelope is None:
                    await self._response_queue.ack(message.id)
                    logger.warning("Dropped a malformed gateway reply")
                    continue
                if envelope.correlation_id == correlation_id:
                    await self._response_queue.ack(message.id)
                    return envelope
                age_seconds = time.time() - envelope.created_at
                if age_seconds > self._orphan_age_seconds:
                    await self._response_queue.ack(message.id)
                    logger.warning(
                        "Dropped an orphaned gateway reply: age=%.0fs limit=%.0fs",
                        age_seconds,
                        self._orphan_age_seconds,
                    )
                    continue
                await self._response_queue.nack(message.id, requeue=True)
                await asyncio.sleep(0.01)

        raise QueueTimeoutError(
            f"gateway did not reply within {self._timeout_seconds}s "
            f"(correlation_id={correlation_id})"
        )


def _parse_reply(payload) -> ResponseEnvelope | None:
    """Parse a reply payload; ``None`` when it is malformed.

    Malformed means: not parseable as a :class:`ResponseEnvelope`, a
    non-string ``text`` / ``error``, or a non-finite ``created_at``.
    """
    try:
        envelope = ResponseEnvelope.from_dict(payload)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    if not math.isfinite(envelope.created_at):
        return None
    if not all(isinstance(value, (str, type(None))) for value in (envelope.text, envelope.error)):
        return None
    return envelope


def client_timeout_from_env() -> float:
    """Read ``SEPTUM_GATEWAY_CLIENT_TIMEOUT_SECONDS``; unset or blank gives the default.

    Raises :class:`GatewayStartupError` for a value that is not a positive number.
    """
    raw = os.getenv(CLIENT_TIMEOUT_ENV, "").strip()
    if not raw:
        return DEFAULT_CLIENT_TIMEOUT_SECONDS
    refusal = GatewayStartupError(f"{CLIENT_TIMEOUT_ENV} must be a positive number of seconds.")
    try:
        value = float(raw)
    except ValueError:
        raise refusal from None
    if not 0 < value < float("inf"):
        raise refusal
    return value


async def build_gateway_client_from_env() -> GatewayClient:
    """Build the process-wide client from the queue and topic environment variables.

    The backends come from :func:`septum_queue.backend_from_env`, so
    ``SEPTUM_QUEUE_URL`` / ``SEPTUM_QUEUE_DIR`` must be set before calling.
    An unusable queue URL raises :class:`GatewayStartupError` whose message
    and traceback carry neither the URL nor its password. The returned
    client owns its backends; close it with :meth:`GatewayClient.aclose`.
    """
    timeout_seconds = client_timeout_from_env()
    request_topic = os.getenv(REQUEST_TOPIC_ENV) or LLM_REQUEST_TOPIC
    response_topic = os.getenv(RESPONSE_TOPIC_ENV) or LLM_RESPONSE_TOPIC
    request_queue = _queue_backend(request_topic)
    try:
        response_queue = _queue_backend(response_topic)
    except BaseException:
        await request_queue.close()
        raise
    return GatewayClient(
        request_queue=request_queue,
        response_queue=response_queue,
        timeout_seconds=timeout_seconds,
    )


def _queue_backend(topic: str) -> QueueBackend:
    """Open ``topic`` via :func:`backend_from_env`, hiding the URL on failure.

    Redis client errors can echo parts of the URL, so the original exception
    is not chained; only its class is kept.
    """
    try:
        return backend_from_env(topic)
    except (QueueError, ValueError) as exc:
        raise GatewayStartupError(
            "The gateway queue settings are not usable: SEPTUM_QUEUE_URL must be a "
            "redis://, rediss:// or unix:// URL with a valid host, port and database, "
            f"and {REQUEST_TOPIC_ENV} / {RESPONSE_TOPIC_ENV} must match "
            f"^[A-Za-z0-9._-]+$ ({type(exc).__name__})."
        ) from None
