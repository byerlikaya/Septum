"""FileQueueBackend round-trip, persistence, and race-safety tests."""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from septum_queue import FileQueueBackend, Message, QueueError, QueueSession
from septum_queue import file_backend as file_backend_module


async def _consume_one(backend: FileQueueBackend, *, block_ms: int = 1000) -> Message:
    async for message in backend.consume(batch_size=1, block_ms=block_ms):
        return message
    raise AssertionError("backend.consume yielded nothing")


class TestFileQueueBackend:
    async def test_publish_then_consume_round_trip(self, tmp_path: Path):
        backend = FileQueueBackend(tmp_path, topic="llm-requests")
        try:
            mid = await backend.publish({"hello": "world"})
            assert mid.endswith(".json")
            message = await _consume_one(backend, block_ms=0)
            assert message.id == mid
            assert message.payload == {"hello": "world"}
            await backend.ack(message.id)
        finally:
            await backend.close()

    async def test_fifo_order_matches_publish_order(self, tmp_path: Path):
        backend = FileQueueBackend(tmp_path, topic="t")
        try:
            ids: list[str] = []
            for i in range(5):
                ids.append(await backend.publish({"i": i}))
            received: list[dict[str, Any]] = []
            async for message in backend.consume(batch_size=5, block_ms=0):
                received.append(dict(message.payload))
                await backend.ack(message.id)
            assert [item["i"] for item in received] == [0, 1, 2, 3, 4]
        finally:
            await backend.close()

    async def test_nack_requeue_returns_message_to_incoming(self, tmp_path: Path):
        backend = FileQueueBackend(tmp_path, topic="t")
        try:
            await backend.publish({"x": 1})
            first = await _consume_one(backend, block_ms=0)
            await backend.nack(first.id, requeue=True)
            second = await _consume_one(backend, block_ms=0)
            assert second.payload == {"x": 1}
        finally:
            await backend.close()

    async def test_nack_requeue_false_drops_message(self, tmp_path: Path):
        backend = FileQueueBackend(tmp_path, topic="t")
        try:
            await backend.publish({"x": 1})
            first = await _consume_one(backend, block_ms=0)
            await backend.nack(first.id, requeue=False)
            # Queue should be empty now; block_ms=0 returns immediately.
            got_any = False
            async for _ in backend.consume(batch_size=1, block_ms=0):
                got_any = True
            assert got_any is False
        finally:
            await backend.close()

    async def test_survives_backend_restart_with_pending_messages(self, tmp_path: Path):
        first = FileQueueBackend(tmp_path, topic="t")
        await first.publish({"persisted": True})
        await first.close()

        second = FileQueueBackend(tmp_path, topic="t")
        try:
            message = await _consume_one(second, block_ms=0)
            assert message.payload == {"persisted": True}
            await second.ack(message.id)
        finally:
            await second.close()

    async def test_two_consumers_race_only_one_wins_each_message(
        self, tmp_path: Path
    ):
        backend = FileQueueBackend(tmp_path, topic="t")
        try:
            total = 30
            for i in range(total):
                await backend.publish({"i": i})

            received_a: list[int] = []
            received_b: list[int] = []

            async def drain(target: list[int]) -> None:
                async for message in backend.consume(batch_size=total, block_ms=0):
                    target.append(int(message.payload["i"]))
                    await backend.ack(message.id)

            await asyncio.gather(drain(received_a), drain(received_b))

            combined = sorted(received_a + received_b)
            assert combined == list(range(total))
            # Race-guard: no duplicate should appear across the two consumers.
            assert len(set(combined)) == total
        finally:
            await backend.close()

    async def test_double_ack_is_idempotent(self, tmp_path: Path):
        backend = FileQueueBackend(tmp_path, topic="t")
        try:
            await backend.publish({"x": 1})
            message = await _consume_one(backend, block_ms=0)
            await backend.ack(message.id)
            # Second ack on the same id must not raise.
            await backend.ack(message.id)
        finally:
            await backend.close()

    async def test_publish_after_close_raises(self, tmp_path: Path):
        backend = FileQueueBackend(tmp_path, topic="t")
        await backend.close()
        with pytest.raises(QueueError):
            await backend.publish({"late": True})

    async def test_queue_session_closes_backend_on_exit(self, tmp_path: Path):
        backend = FileQueueBackend(tmp_path, topic="t")
        async with QueueSession(backend):
            await backend.publish({"inside": True})
        with pytest.raises(QueueError):
            await backend.publish({"after": True})

    async def test_partial_write_is_not_delivered(self, tmp_path: Path):
        """A .json.tmp file in incoming/ must not be visible to consumers."""
        backend = FileQueueBackend(tmp_path, topic="t")
        try:
            stray = tmp_path / "t" / "incoming" / "half-written.json.tmp"
            stray.write_text("{not", encoding="utf-8")
            got_any = False
            async for _ in backend.consume(batch_size=1, block_ms=0):
                got_any = True
            assert got_any is False
            assert stray.exists()  # tmp sibling left alone by the scanner
        finally:
            await backend.close()


class _Gate:
    """Holds a backend thread at a known point until the test releases it."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()


def _hold_stage(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    gate: _Gate,
    *,
    fail: BaseException | None = None,
    before: bool = False,
) -> None:
    """Hold ``stage``'s thread after its real work (or before it, with ``before``)."""
    original = getattr(FileQueueBackend, stage)

    def held(self, *args):
        if before:
            gate.entered.set()
            gate.release.wait(5)
            result = original(self, *args)
        else:
            result = original(self, *args)
            gate.entered.set()
            gate.release.wait(5)
        if fail is not None:
            raise fail
        return result

    monkeypatch.setattr(FileQueueBackend, stage, held)


def _signal_after(monkeypatch: pytest.MonkeyPatch, owner, name: str, event: threading.Event) -> None:
    """Set ``event`` once ``owner.name`` has returned or raised; the wrapped call is unchanged."""
    original = getattr(owner, name)

    if asyncio.iscoroutinefunction(original):

        async def signalling(*args, **kwargs):
            try:
                return await original(*args, **kwargs)
            finally:
                event.set()

    else:

        def signalling(*args, **kwargs):
            try:
                return original(*args, **kwargs)
            finally:
                event.set()

    monkeypatch.setattr(owner, name, signalling)


async def _wait_for(event: threading.Event) -> None:
    """Await a threading.Event from the loop, without using any executor."""
    loop = asyncio.get_running_loop()
    arrived = asyncio.Event()

    def relay() -> None:
        if event.wait(5):
            loop.call_soon_threadsafe(arrived.set)

    threading.Thread(target=relay, daemon=True).start()
    await asyncio.wait_for(arrived.wait(), 5)


async def _take_one(backend: FileQueueBackend) -> None:
    async for _message in backend.consume(batch_size=1, block_ms=0):
        return


_STAGE = {"ack": "_ack_sync", "nack": "_nack_sync"}


def _ack_or_nack(backend: FileQueueBackend, operation: str, message_id: str):
    if operation == "ack":
        return backend.ack(message_id)
    return backend.nack(message_id, requeue=True)


def _assert_handed_back(topic: Path) -> None:
    assert list((topic / "processing").iterdir()) == []
    assert len(list((topic / "incoming").glob("*.json"))) == 1


class _SubmitCountingPool(ThreadPoolExecutor):
    """Single worker that reports when two jobs have been submitted since ``arm()``."""

    def __init__(self) -> None:
        super().__init__(max_workers=1)
        self._armed = False
        self._submitted = 0
        self.two_submitted = threading.Event()

    def arm(self) -> None:
        self._armed = True
        self._submitted = 0

    def submit(self, fn, /, *args, **kwargs):
        if self._armed:
            self._submitted += 1
            if self._submitted == 2:
                self.two_submitted.set()
        return super().submit(fn, *args, **kwargs)


class TestCancelledConsumer:
    """A cancelled caller returns at once; the thread finishes; no entry is stranded."""

    @pytest.mark.parametrize("stage", ["_claim", "_load_claimed"])
    async def test_claimed_entry_is_handed_back_after_a_prompt_cancel(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
    ):
        backend = FileQueueBackend(tmp_path, topic="t")
        gate = _Gate()
        handed_back = threading.Event()
        try:
            await backend.publish({"k": "v"})
            _hold_stage(monkeypatch, stage, gate)
            _signal_after(monkeypatch, FileQueueBackend, "_nack_sync", handed_back)
            consumer = asyncio.create_task(_take_one(backend))
            await _wait_for(gate.entered)
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(consumer, 5)
            assert not gate.release.is_set()
            gate.release.set()
            await _wait_for(handed_back)

            _assert_handed_back(tmp_path / "t")
            monkeypatch.undo()
            assert (await _consume_one(backend, block_ms=0)).payload == {"k": "v"}
        finally:
            gate.release.set()
            await backend.close()

    async def test_stalled_ack_cancel_returns_promptly_and_still_completes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        backend = FileQueueBackend(tmp_path, topic="t")
        gate = _Gate()
        acked = threading.Event()
        try:
            await backend.publish({"k": "v"})
            message = await _consume_one(backend, block_ms=0)
            _hold_stage(monkeypatch, "_ack_sync", gate)
            _signal_after(monkeypatch, FileQueueBackend, "_ack_sync", acked)
            operation = asyncio.create_task(backend.ack(message.id))
            await _wait_for(gate.entered)
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(operation, 5)
            gate.release.set()
            await _wait_for(acked)

            assert list((tmp_path / "t" / "processing").iterdir()) == []
            assert len(list((tmp_path / "t" / "done").iterdir())) == 1
        finally:
            gate.release.set()
            await backend.close()

    async def test_malformed_entry_cancelled_during_load_is_quarantined(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        backend = FileQueueBackend(tmp_path, topic="t")
        gate = _Gate()
        try:
            (tmp_path / "t" / "incoming" / "0000-bad.json").write_bytes(b"{not json")
            _hold_stage(monkeypatch, "_load_claimed", gate)
            consumer = asyncio.create_task(_take_one(backend))
            await _wait_for(gate.entered)
            consumer.cancel()
            gate.release.set()
            with pytest.raises(asyncio.CancelledError):
                await consumer

            assert len(list((tmp_path / "t" / "dead-letter").iterdir())) == 1
            assert list((tmp_path / "t" / "incoming").iterdir()) == []
            assert list((tmp_path / "t" / "processing").iterdir()) == []
        finally:
            gate.release.set()
            await backend.close()

    @pytest.mark.parametrize("cancelled", [True, False])
    async def test_claimed_then_load_raises_ends_where_the_uncancelled_path_does(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        cancelled: bool,
    ):
        backend = FileQueueBackend(tmp_path, topic="t")
        gate = _Gate()
        logged = threading.Event()
        try:
            await backend.publish({"k": "v"})
            _hold_stage(monkeypatch, "_load_claimed", gate, fail=RuntimeError("boom"))
            _signal_after(monkeypatch, FileQueueBackend, "_hand_back_when_done", logged)
            consumer = asyncio.create_task(_take_one(backend))
            with caplog.at_level(logging.ERROR, logger="septum_queue.file_backend"):
                if cancelled:
                    await _wait_for(gate.entered)
                    consumer.cancel()
                    gate.release.set()
                    with pytest.raises(asyncio.CancelledError):
                        await consumer
                    await _wait_for(logged)
                else:
                    gate.release.set()
                    with pytest.raises(RuntimeError):
                        await consumer

            assert len(list((tmp_path / "t" / "processing").iterdir())) == 1
            assert list((tmp_path / "t" / "incoming").iterdir()) == []
            if cancelled:
                assert "RuntimeError" in caplog.text
            assert "boom" not in caplog.text
        finally:
            gate.release.set()
            await backend.close()

    async def test_failed_hand_back_is_logged_by_type_and_payload_stays_out(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        backend = FileQueueBackend(tmp_path, topic="t")
        gate = _Gate()
        finished = threading.Event()
        try:
            await backend.publish({"secret": "PAYLOAD-SENTINEL"})
            _hold_stage(monkeypatch, "_load_claimed", gate)

            def refuse(self, message_id, requeue):
                raise OSError(f"refused {message_id}")

            monkeypatch.setattr(FileQueueBackend, "_nack_sync", refuse)
            _signal_after(monkeypatch, FileQueueBackend, "_hand_back", finished)
            consumer = asyncio.create_task(_take_one(backend))
            with caplog.at_level(logging.ERROR, logger="septum_queue.file_backend"):
                await _wait_for(gate.entered)
                consumer.cancel()
                gate.release.set()
                with pytest.raises(asyncio.CancelledError):
                    await consumer
                await _wait_for(finished)

            assert "Could not hand back queue message" in caplog.text
            assert "OSError" in caplog.text
            assert "PAYLOAD-SENTINEL" not in caplog.text
        finally:
            gate.release.set()
            await backend.close()

    async def test_second_cancel_during_hand_back_still_returns_the_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        backend = FileQueueBackend(tmp_path, topic="t")
        load_gate = _Gate()
        nack_gate = _Gate()
        handed_back = threading.Event()
        try:
            await backend.publish({"k": "v"})
            _hold_stage(monkeypatch, "_load_claimed", load_gate)
            _hold_stage(monkeypatch, "_nack_sync", nack_gate, before=True)
            _signal_after(monkeypatch, FileQueueBackend, "_nack_sync", handed_back)
            consumer = asyncio.create_task(_take_one(backend))
            await _wait_for(load_gate.entered)
            consumer.cancel()
            load_gate.release.set()
            with pytest.raises(asyncio.CancelledError):
                await consumer
            await _wait_for(nack_gate.entered)
            for hand_back in list(backend._background):
                hand_back.cancel()
            nack_gate.release.set()
            await _wait_for(handed_back)

            _assert_handed_back(tmp_path / "t")
        finally:
            nack_gate.release.set()
            await backend.close()

    @pytest.mark.parametrize("operation", ["ack", "nack"])
    async def test_failed_ack_or_nack_after_cancel_is_logged_by_type_only(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        operation: str,
    ):
        backend = FileQueueBackend(tmp_path, topic="t")
        gate = _Gate()
        logged = threading.Event()
        try:
            await backend.publish({"k": "v"})
            message = await _consume_one(backend, block_ms=0)
            _hold_stage(monkeypatch, _STAGE[operation], gate, fail=OSError("refused /secret/path"))
            _signal_after(monkeypatch, file_backend_module, "_log_failure_after_cancel", logged)
            call_task = asyncio.create_task(_ack_or_nack(backend, operation, message.id))
            with caplog.at_level(logging.ERROR, logger="septum_queue.file_backend"):
                await _wait_for(gate.entered)
                call_task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(call_task, 5)
                gate.release.set()
                await _wait_for(logged)

            assert "OSError" in caplog.text
            assert "secret" not in caplog.text
        finally:
            gate.release.set()
            await backend.close()

    @pytest.mark.parametrize("operation", ["ack", "nack"])
    async def test_normal_path_ack_or_nack_failure_propagates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
    ):
        backend = FileQueueBackend(tmp_path, topic="t")
        try:
            await backend.publish({"k": "v"})
            message = await _consume_one(backend, block_ms=0)

            def refuse(self, *args):
                raise OSError("refused")

            monkeypatch.setattr(FileQueueBackend, _STAGE[operation], refuse)
            with pytest.raises(OSError):
                await _ack_or_nack(backend, operation, message.id)
        finally:
            await backend.close()

    @pytest.mark.parametrize("operation", ["ack", "nack"])
    async def test_cancelled_ack_or_nack_queued_behind_busy_worker_is_not_stranded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
    ):
        backend = FileQueueBackend(tmp_path, topic="t")
        loop = asyncio.get_running_loop()
        pool = _SubmitCountingPool()
        loop.set_default_executor(pool)
        release = threading.Event()
        finished = threading.Event()
        _signal_after(monkeypatch, FileQueueBackend, _STAGE[operation], finished)
        try:
            await backend.publish({"k": "v"})
            message = await _consume_one(backend, block_ms=0)
            pool.arm()
            pool.submit(release.wait, 5)
            call_task = asyncio.create_task(_ack_or_nack(backend, operation, message.id))
            await _wait_for(pool.two_submitted)
            call_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(call_task, 5)
            release.set()
            await _wait_for(finished)

            topic = tmp_path / "t"
            assert list((topic / "processing").iterdir()) == []
            if operation == "ack":
                assert len(list((topic / "done").iterdir())) == 1
            else:
                assert len(list((topic / "incoming").iterdir())) == 1
        finally:
            release.set()
            pool.shutdown(wait=True)
            await backend.close()
