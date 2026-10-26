"""FileQueueBackend runs its blocking I/O on its own bounded pool, not the loop's default."""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from septum_queue import FileQueueBackend, Message, QueueError
from septum_queue import file_backend as file_backend_module

LIMIT = file_backend_module._IO_WORKERS


async def _wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)


async def _take_one(backend: FileQueueBackend) -> Message:
    async for message in backend.consume(batch_size=1, block_ms=0):
        return message
    raise AssertionError("backend.consume yielded nothing")


async def test_queue_ops_progress_while_default_executor_is_saturated(tmp_path: Path):
    loop = asyncio.get_running_loop()
    default = ThreadPoolExecutor(max_workers=1, thread_name_prefix="default-saturated")
    loop.set_default_executor(default)
    release = threading.Event()
    blocker = loop.run_in_executor(None, release.wait, 10)
    backend = FileQueueBackend(tmp_path, topic="t")
    try:
        await asyncio.sleep(0.05)
        assert not blocker.done()

        await asyncio.wait_for(backend.publish({"k": "v"}), 5)
        message = await asyncio.wait_for(_take_one(backend), 5)
        await asyncio.wait_for(backend.ack(message.id), 5)

        assert len(list((tmp_path / "t" / "done").iterdir())) == 1
    finally:
        release.set()
        await blocker
        await backend.close()
        default.shutdown(wait=True)


async def test_blocking_calls_run_on_named_executor_threads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    original = FileQueueBackend._publish_sync
    thread_names: list[str] = []

    def recording(self, payload):
        thread_names.append(threading.current_thread().name)
        return original(self, payload)

    monkeypatch.setattr(FileQueueBackend, "_publish_sync", recording)
    backend = FileQueueBackend(tmp_path, topic="t")
    try:
        await backend.publish({"k": "v"})
    finally:
        await backend.close()

    assert len(thread_names) == 1
    assert thread_names[0].startswith(file_backend_module._IO_THREAD_PREFIX)


async def test_blocked_calls_cannot_grow_the_pool_past_its_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    gate = threading.Event()
    lock = threading.Lock()
    entered_threads: list[int] = []
    observed_names: list[str] = []
    all_workers_entered = threading.Event()

    def blocked(self, payload):
        with lock:
            observed_names.append(threading.current_thread().name)
            entered_threads.append(threading.get_ident())
            if len(entered_threads) == LIMIT:
                all_workers_entered.set()
        gate.wait(10)
        return "id"

    monkeypatch.setattr(FileQueueBackend, "_publish_sync", blocked)
    backend = FileQueueBackend(tmp_path, topic="t")
    tasks = [asyncio.create_task(backend.publish({"i": i})) for i in range(LIMIT + 3)]
    try:
        await _wait_until(all_workers_entered.is_set)
        await asyncio.sleep(0.2)

        assert len(entered_threads) == LIMIT
        assert len(set(entered_threads)) == LIMIT
    finally:
        gate.set()
        await asyncio.gather(*tasks)
        await backend.close()

    assert len(set(entered_threads)) == LIMIT
    assert len(observed_names) == LIMIT + 3
    assert set(observed_names) == {f"{file_backend_module._IO_THREAD_PREFIX}_{i}" for i in range(LIMIT)}


async def test_close_returns_promptly_while_an_io_call_is_stalled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    original = FileQueueBackend._publish_sync
    gate = threading.Event()
    entered = threading.Event()

    def stalled(self, payload):
        entered.set()
        gate.wait(10)
        return original(self, payload)

    monkeypatch.setattr(FileQueueBackend, "_publish_sync", stalled)
    backend = FileQueueBackend(tmp_path, topic="t")
    publish = asyncio.create_task(backend.publish({"k": "v"}))
    try:
        await _wait_until(entered.is_set)

        started = time.monotonic()
        await asyncio.wait_for(backend.close(), 2)
        assert time.monotonic() - started < 1
        assert not publish.done()

        with pytest.raises(QueueError):
            await backend.publish({"k": "late"})
        with pytest.raises(QueueError):
            async with contextlib.aclosing(backend.consume(batch_size=1, block_ms=0)) as stream:
                await stream.__anext__()
    finally:
        gate.set()

    mid = await asyncio.wait_for(publish, 5)
    assert (tmp_path / "t" / "incoming" / mid).exists()
    await backend.close()


def test_backend_works_across_successive_event_loops(tmp_path: Path):
    backend = FileQueueBackend(tmp_path, topic="t")

    async def produce() -> str:
        return await backend.publish({"k": 1})

    async def consume_and_ack() -> Message:
        message = await _take_one(backend)
        await backend.ack(message.id)
        return message

    try:
        asyncio.run(produce())
        message = asyncio.run(consume_and_ack())
        assert message.payload == {"k": 1}
    finally:
        asyncio.run(backend.close())


def test_constructing_a_backend_spawns_no_worker_threads(tmp_path: Path):
    def prefixed_idents() -> set[int]:
        return {
            t.ident
            for t in threading.enumerate()
            if t.name.startswith(file_backend_module._IO_THREAD_PREFIX)
        }

    before = prefixed_idents()
    backend = FileQueueBackend(tmp_path, topic="t")
    try:
        assert prefixed_idents() - before == set()
    finally:
        asyncio.run(backend.close())
