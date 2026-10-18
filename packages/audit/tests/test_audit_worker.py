"""Shutdown semantics of the septum-audit worker entry point."""

from __future__ import annotations

import asyncio

import pytest
from septum_audit.worker import _await_stopped


async def _forever() -> None:
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_self_cancelled_worker_stops_cleanly() -> None:
    worker = asyncio.create_task(_forever())
    await asyncio.sleep(0)
    worker.cancel()

    await _await_stopped(worker)

    assert worker.cancelled()


@pytest.mark.asyncio
async def test_cancellation_of_the_caller_propagates() -> None:
    worker = asyncio.create_task(_forever())
    stopper = asyncio.create_task(_await_stopped(worker))
    await asyncio.sleep(0)
    stopper.cancel()

    with pytest.raises(asyncio.CancelledError):
        await stopper

    worker.cancel()
    await asyncio.wait({worker})


@pytest.mark.asyncio
async def test_worker_failure_is_not_swallowed() -> None:
    async def _fail() -> None:
        raise RuntimeError("boom")

    worker = asyncio.create_task(_fail())

    with pytest.raises(RuntimeError):
        await _await_stopped(worker)


@pytest.mark.asyncio
async def test_caller_cancel_waits_for_worker_before_cleanup() -> None:
    async def _slow_unwind() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0.01)

    worker = asyncio.create_task(_slow_unwind())
    done_at_cleanup: list[bool] = []

    async def _stop() -> None:
        try:
            await _await_stopped(worker)
        finally:
            done_at_cleanup.append(worker.done())

    stopper = asyncio.create_task(_stop())
    await asyncio.sleep(0)
    stopper.cancel()

    with pytest.raises(asyncio.CancelledError):
        await stopper

    assert done_at_cleanup == [True]
