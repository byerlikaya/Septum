"""Long-running worker that wires queue backends into a :class:`GatewayConsumer`.

Entry point for ``python -m septum_gateway``. Reads queue connection
details from the environment, constructs the queue backends + forwarder
registry, and blocks in ``run_forever`` until signalled.

Queue backend selection:

* ``SEPTUM_QUEUE_URL=redis://host:6379/0`` → :class:`RedisStreamsQueueBackend`
* ``SEPTUM_QUEUE_DIR=/srv/septum/queue`` → :class:`FileQueueBackend`

If both are set, the Redis URL wins. If neither is set, the worker
exits with a clear error rather than silently defaulting — an
air-gapped deployment without a declared queue is almost always a
misconfiguration.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from typing import TYPE_CHECKING

from .config import GatewayConfig
from .forwarder import ForwarderRegistry
from .response_handler import GatewayConsumer

if TYPE_CHECKING:
    from septum_queue import QueueBackend

logger = logging.getLogger(__name__)


def _build_queue(topic: str) -> "QueueBackend":
    """Pick a queue backend from the environment, erroring if none is declared."""
    redis_url = os.getenv("SEPTUM_QUEUE_URL")
    queue_dir = os.getenv("SEPTUM_QUEUE_DIR")
    if redis_url:
        from septum_queue import RedisStreamsQueueBackend

        return RedisStreamsQueueBackend.from_url(redis_url, topic=topic)
    if queue_dir:
        from septum_queue import FileQueueBackend

        return FileQueueBackend(queue_dir, topic=topic)
    raise SystemExit(
        "septum-gateway worker: set SEPTUM_QUEUE_URL (redis://…) "
        "or SEPTUM_QUEUE_DIR (filesystem path) before starting."
    )


async def _run(config: GatewayConfig) -> None:
    request_queue = _build_queue(config.request_topic)
    response_queue = _build_queue(config.response_topic)
    audit_queue = (
        _build_queue(config.audit_topic) if config.audit_topic else None
    )
    registry = ForwarderRegistry.from_config(config)
    consumer = GatewayConsumer(
        request_queue=request_queue,
        response_queue=response_queue,
        registry=registry,
        audit_queue=audit_queue,
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # Windows / sandboxed contexts: rely on KeyboardInterrupt.
            pass

    logger.info(
        "septum-gateway worker started: request=%s response=%s audit=%s",
        config.request_topic,
        config.response_topic,
        config.audit_topic,
    )

    worker = asyncio.create_task(consumer.run_forever())
    await stop_event.wait()
    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass
    finally:
        for q in (request_queue, response_queue, audit_queue):
            if q is None:
                continue
            try:
                await q.close()
            except Exception:  # noqa: BLE001
                logger.warning("queue close failed", exc_info=True)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.getenv("SEPTUM_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = GatewayConfig.from_env()
    try:
        asyncio.run(_run(config))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
