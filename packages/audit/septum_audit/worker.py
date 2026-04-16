"""Long-running worker that drives an :class:`AuditConsumer`.

Entry point for ``python -m septum_audit``. Constructs a queue backend
from the environment (Redis Streams or filesystem), opens the
configured sink, and blocks in ``run_forever`` until signalled.

Queue backend selection mirrors :mod:`septum_gateway.worker`:

* ``SEPTUM_QUEUE_URL=redis://host:6379/0`` → Redis Streams
* ``SEPTUM_QUEUE_DIR=/srv/septum/queue`` → filesystem
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from typing import TYPE_CHECKING

from .config import AuditConfig
from .consumer import AuditConsumer
from .sink import JsonlFileSink

if TYPE_CHECKING:
    from septum_queue import QueueBackend

logger = logging.getLogger(__name__)


def _build_queue(topic: str) -> "QueueBackend":
    redis_url = os.getenv("SEPTUM_QUEUE_URL")
    queue_dir = os.getenv("SEPTUM_QUEUE_DIR")
    if redis_url:
        from septum_queue import RedisStreamsQueueBackend

        return RedisStreamsQueueBackend.from_url(redis_url, topic=topic)
    if queue_dir:
        from septum_queue import FileQueueBackend

        return FileQueueBackend(queue_dir, topic=topic)
    raise SystemExit(
        "septum-audit worker: set SEPTUM_QUEUE_URL (redis://…) "
        "or SEPTUM_QUEUE_DIR (filesystem path) before starting."
    )


async def _run(config: AuditConfig) -> None:
    queue = _build_queue(config.audit_topic)
    sink = JsonlFileSink(config.sink_path)
    consumer = AuditConsumer(queue=queue, sink=sink)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    logger.info(
        "septum-audit worker started: topic=%s sink=%s",
        config.audit_topic,
        config.sink_path,
    )

    worker = asyncio.create_task(consumer.run_forever())
    await stop_event.wait()
    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass
    finally:
        try:
            await queue.close()
        except Exception:  # noqa: BLE001
            logger.warning("queue close failed", exc_info=True)
        await sink.close()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.getenv("SEPTUM_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = AuditConfig.from_env()
    try:
        asyncio.run(_run(config))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
