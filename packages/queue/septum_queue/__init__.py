"""Septum message queue: the cross-zone bridge for masked LLM traffic."""

from __future__ import annotations

import os
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
    "backend_from_env",
]


def backend_from_env(topic: str) -> QueueBackend:
    """Pick a backend from ``SEPTUM_QUEUE_URL`` / ``SEPTUM_QUEUE_DIR``.

    Redis URL wins when both are set. Missing both → :class:`SystemExit`
    rather than a silent default — an air-gapped worker without a
    declared queue is almost always a misconfiguration.
    """
    redis_url = os.getenv("SEPTUM_QUEUE_URL")
    queue_dir = os.getenv("SEPTUM_QUEUE_DIR")
    if redis_url:
        from .redis_backend import RedisStreamsQueueBackend

        return RedisStreamsQueueBackend.from_url(redis_url, topic=topic)
    if queue_dir:
        from .file_backend import FileQueueBackend

        return FileQueueBackend(queue_dir, topic=topic)
    raise SystemExit(
        "septum-queue: set SEPTUM_QUEUE_URL (redis://…) or "
        "SEPTUM_QUEUE_DIR (filesystem path) before starting."
    )

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
