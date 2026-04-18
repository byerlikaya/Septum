"""Entry point for ``python -m septum_gateway``: long-running consumer loop."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

from septum_queue import backend_from_env

from .config import GatewayConfig
from .forwarder import ForwarderRegistry
from .response_handler import GatewayConsumer

logger = logging.getLogger(__name__)


async def _run(config: GatewayConfig) -> None:
    request_queue = backend_from_env(config.request_topic)
    response_queue = backend_from_env(config.response_topic)
    audit_queue = (
        backend_from_env(config.audit_topic) if config.audit_topic else None
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
            # Windows proactor loops: KeyboardInterrupt is the fallback signal.
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


def main() -> int:
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
