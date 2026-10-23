"""Entry point for ``python -m septum_audit``: drives an :class:`AuditConsumer`."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import signal
import sys
import time
from pathlib import Path

from septum_queue import backend_from_env

from .config import AuditConfig
from .consumer import AuditConsumer
from .events import LedgerIntegrityError
from .retention import RetentionPolicy, pin_ahead_seconds
from .sink import JsonlFileSink
from .status import RetentionStatus, write_status

logger = logging.getLogger(__name__)

# A pass may not take more than one tenth of the time between passes, so a slow
# pass cannot hold the ledger lock for long stretches.
PASS_DUTY_CYCLE = 10.0


def _next_delay(interval_seconds: int, pass_seconds: float) -> float:
    """Seconds to sleep after a pass: at least one interval, and at least ``PASS_DUTY_CYCLE`` times the pass."""
    return max(float(interval_seconds), PASS_DUTY_CYCLE * pass_seconds)


def _report_status(
    ledger: Path,
    state: str,
    last_success_at: float | None,
    consecutive_failures: int,
    interval_seconds: int,
) -> None:
    """Write the content-free status sidecar with the check time.

    A failed write is logged and does not stop pruning; the next tick retries it.
    """
    try:
        write_status(
            ledger,
            RetentionStatus(
                state=state,
                last_success_at=last_success_at,
                consecutive_failures=consecutive_failures,
                checked_at=time.time(),
                interval_seconds=interval_seconds,
            ),
        )
    except OSError as exc:
        logger.error(
            "audit retention status could not be written: sink=%s error=%s",
            ledger,
            type(exc).__name__,
        )


async def _disabled_loop(ledger: Path, interval_seconds: int) -> None:
    """Heartbeat for a worker without retention, so the status never looks stale."""
    while True:
        await asyncio.to_thread(_report_status, ledger, "disabled", None, 0, interval_seconds)
        await asyncio.sleep(interval_seconds)


async def _retention_loop(
    sink: JsonlFileSink,
    policy: RetentionPolicy,
    interval_seconds: int,
) -> None:
    """Run ``policy`` at startup and then once per period, until cancelled.

    A period runs from the start of one pass to the start of the next and lasts
    ``max(interval_seconds, 10 x the pass duration)``. Each tick writes one status:
    ``ok``, ``halted`` (verification failed, so pruning stopped), ``failed`` (any other
    error), or ``blocked`` (the pass succeeded, but the oldest record is more than
    ``FUTURE_PIN_SECONDS`` ahead of the clock). Errors are logged by exception type
    only. The loop does not exit on a failure.
    """
    ledger = Path(sink.path)
    last_success_at: float | None = None
    consecutive_failures = 0
    while True:
        started = time.monotonic()
        try:
            outcome = await sink.apply_retention_outcome(policy)
        except LedgerIntegrityError as exc:
            state = "halted"
            logger.error(
                "audit retention halted until the ledger is repaired: sink=%s reason=%s",
                sink.path,
                exc,
            )
        except Exception as exc:
            state = "failed"
            logger.error(
                "audit retention run failed, retrying next run: sink=%s error=%s",
                sink.path,
                type(exc).__name__,
            )
        else:
            state = "ok"
            last_success_at = time.time()
            if outcome.pruned or outcome.torn:
                logger.info(
                    "audit retention pruned %d record(s) and dropped %d torn line(s)",
                    outcome.pruned,
                    outcome.torn,
                )
        pass_seconds = time.monotonic() - started
        # A pin only means something when an age cap is set: the count cap alone never reads ages.
        if state == "ok" and policy.max_age_days is not None:
            ahead = await asyncio.to_thread(pin_ahead_seconds, ledger, time.time())
            if ahead is not None:
                state = "blocked"
                logger.error(
                    "audit retention blocked: the oldest record is stamped %s s ahead of the clock; "
                    "records after it are not pruned until the clock catches up or the ledger is restored",
                    ahead,
                )
        consecutive_failures = consecutive_failures + 1 if state in ("halted", "failed") else 0
        period = _next_delay(interval_seconds, pass_seconds)
        await asyncio.to_thread(
            _report_status,
            ledger,
            state,
            last_success_at,
            consecutive_failures,
            math.ceil(period),
        )
        # Start to start: the next pass begins one period after this one began.
        await asyncio.sleep(max(0.0, period - (time.monotonic() - started)))


def _task_error(task: asyncio.Task[None]) -> str:
    if task.cancelled():
        return "cancelled"
    error = task.exception()
    return type(error).__name__ if error is not None else "returned"


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

    policy = RetentionPolicy(
        max_age_days=config.retention_max_age_days,
        max_records=config.retention_max_records,
    )
    if policy.is_noop:
        retention = asyncio.create_task(
            _disabled_loop(Path(config.sink_path), config.retention_interval_seconds)
        )
    else:
        logger.info(
            "audit retention enabled: max_age_days=%s max_records=%s interval_seconds=%d",
            config.retention_max_age_days,
            config.retention_max_records,
            config.retention_interval_seconds,
        )
        retention = asyncio.create_task(
            _retention_loop(sink, policy, config.retention_interval_seconds)
        )

    worker = asyncio.create_task(consumer.run_forever())
    stop_task = asyncio.create_task(stop_event.wait())
    unexpected: str | None = None
    try:
        done, _ = await asyncio.wait({stop_task, worker, retention}, return_when=asyncio.FIRST_COMPLETED)
        if stop_task not in done:
            failed, name = (worker, "consumer") if worker in done else (retention, "retention")
            unexpected = name
            logger.error(
                "septum-audit %s task stopped unexpectedly (%s); exiting so the supervisor restarts the process",
                name,
                _task_error(failed),
            )
    finally:
        for task in (worker, retention, stop_task):
            task.cancel()
        await asyncio.wait({worker, retention, stop_task})
        for task in (worker, retention):
            if task.done() and not task.cancelled() and task.exception() is not None:
                logger.error(
                    "septum-audit task ended with an error during shutdown: %s",
                    type(task.exception()).__name__,
                )
        try:
            await queue.close()
        except Exception as exc:
            logger.warning("queue close failed: %s", type(exc).__name__)
        await sink.close()
    if unexpected is not None:
        raise RuntimeError(f"septum-audit {unexpected} task stopped unexpectedly")


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
