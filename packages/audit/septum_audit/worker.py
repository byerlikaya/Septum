"""Entry point for ``python -m septum_audit``: drives an :class:`AuditConsumer`."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

from septum_queue import backend_from_env

from .config import AuditConfig
from .consumer import AuditConsumer
from .sink import JsonlFileSink

logger = logging.getLogger(__name__)


async def _await_stopped(worker: asyncio.Task[None]) -> None:
    """Wait for a worker we cancelled ourselves; propagate any other exception.

    ``asyncio.wait`` never raises the worker's own cancellation, so only a
    cancellation of the caller (or a real worker failure) leaves this function.
    A cancelled caller still waits for the worker to finish before re-raising,
    so cleanup never runs while the worker is unwinding.
    """
    try:
        await asyncio.wait({worker})
    except asyncio.CancelledError:
        worker.cancel()
        await asyncio.wait({worker})
        raise
    if not worker.cancelled():
        worker.result()


async def _run(config: AuditConfig) -> None:
    queue = backend_from_env(config.audit_topic)
    sink = JsonlFileSink(config.sink_path)
    consumer = AuditConsumer(queue=queue, sink=sink)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # Windows proactor loops: KeyboardInterrupt is the fallback signal.
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
        await _await_stopped(worker)
    finally:
        try:
            await queue.close()
        except Exception as exc:
            logger.warning("queue close failed: %s", type(exc).__name__)
        await sink.close()


def main() -> int:
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
