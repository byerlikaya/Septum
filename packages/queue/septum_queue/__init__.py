"""Septum message queue: the cross-zone bridge for masked LLM traffic.

``septum-queue`` defines the abstract :class:`QueueBackend` protocol and
the envelope dataclasses that carry already-masked chat requests and
responses between the air-gapped api and the internet-facing gateway.
Concrete backends (file, Redis streams, …) are imported lazily so an
air-gapped install that only uses the file backend does not pay the
cost of the Redis client library at import time.

The package itself has zero network dependencies and never imports
``septum-core``; its sole job is transport. See the module-level
docstrings in :mod:`septum_queue.base` and :mod:`septum_queue.models`
for the contract each backend must honor.
"""

from __future__ import annotations

from typing import Any

from .base import QueueBackend, QueueError, QueueSession, QueueTimeoutError
from .models import Message, RequestEnvelope, ResponseEnvelope

__all__ = [
    "QueueBackend",
    "QueueError",
    "QueueSession",
    "QueueTimeoutError",
    "Message",
    "RequestEnvelope",
    "ResponseEnvelope",
    "FileQueueBackend",
    "RedisStreamsQueueBackend",
]

__version__ = "0.1.0"


def __getattr__(name: str) -> Any:
    """Lazy resolver for concrete backends.

    ``FileQueueBackend`` is stdlib-only and could be imported eagerly,
    but keeping every backend behind the same ``__getattr__`` hook
    makes the import surface consistent — callers that never touch
    Redis never import ``redis.asyncio``.
    """
    if name == "FileQueueBackend":
        from .file_backend import FileQueueBackend

        return FileQueueBackend
    if name == "RedisStreamsQueueBackend":
        from .redis_backend import RedisStreamsQueueBackend

        return RedisStreamsQueueBackend
    raise AttributeError(f"module 'septum_queue' has no attribute {name!r}")
