"""Abstract queue interface shared by every concrete backend.

Concrete backends (file, Redis streams, RabbitMQ, HTTP) plug into this
protocol so the producer and consumer code paths stay identical across
deployments. Only primitive JSON-serializable dicts cross the boundary;
envelope shaping is the caller's responsibility (see
:mod:`septum_queue.models`).

The contract is deliberately narrow:

* :meth:`publish` is fire-and-forget — it does not block on delivery.
* :meth:`consume` yields :class:`Message` entries that MUST be acked or
  nacked before the backend can reclaim them.
* :meth:`ack` / :meth:`nack` are idempotent; calling them twice on the
  same id is a no-op.
* :meth:`close` releases any underlying file handles or network
  connections. Backends should be re-entrant: closing twice is safe.

Backends decide their own delivery semantics (at-most-once vs at-least-
once). The file backend gives at-least-once with a visible processing
directory; the Redis streams backend gives at-least-once via consumer
groups + XACK. No backend currently offers exactly-once.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import AsyncIterator, Mapping, Protocol, runtime_checkable

from .models import Message


class QueueError(Exception):
    """Base exception for queue backend failures."""


class QueueTimeoutError(QueueError):
    """Raised when a blocking receive exceeds its deadline.

    The producer side of the gateway RPC uses this to distinguish a
    genuine gateway outage from a still-in-flight request.
    """


@runtime_checkable
class QueueBackend(Protocol):
    """Protocol every concrete backend implements.

    Implementations may be async-native (Redis) or wrap blocking I/O in
    a threadpool (file backend). Either way the public surface is
    ``await``-friendly so callers never branch on backend type.
    """

    topic: str

    async def publish(self, payload: Mapping[str, object]) -> str:
        """Enqueue a JSON-serializable payload and return the backend id.

        The return value is opaque to callers but can be logged for
        operator debugging.
        """
        ...

    def consume(
        self,
        *,
        batch_size: int = 1,
        block_ms: int | None = None,
    ) -> AsyncIterator[Message]:
        """Async-iterate over pending messages.

        ``batch_size`` is an advisory upper bound on how many messages
        the backend fetches per round trip; single-consumer backends
        treat it as 1. ``block_ms`` lets the consumer wait that long
        for new entries before yielding control — pass ``None`` for
        indefinite blocking, ``0`` for a non-blocking poll.
        """
        ...

    async def ack(self, message_id: str) -> None:
        """Mark a delivered message as successfully processed."""
        ...

    async def nack(self, message_id: str, *, requeue: bool = True) -> None:
        """Return a delivered message to the queue (``requeue=True``) or drop it."""
        ...

    async def close(self) -> None:
        """Release any backend-held resources. Safe to call twice."""
        ...


class QueueSession(AbstractAsyncContextManager["QueueSession"]):
    """Convenience wrapper that closes the backend on context exit.

    Callers that use ``async with QueueSession(backend):`` get deterministic
    cleanup even when an exception bubbles out of the consume loop. The
    wrapper forwards every method via attribute delegation so existing
    producer / consumer code does not need to learn a second surface.
    """

    def __init__(self, backend: QueueBackend) -> None:
        self._backend = backend

    @property
    def backend(self) -> QueueBackend:
        return self._backend

    async def __aenter__(self) -> "QueueSession":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._backend.close()
