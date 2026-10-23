"""Worker supervision and scheduling: exit on a dead task, shutdown order, the run period and duty cycle, and the failure counter."""

from __future__ import annotations

import asyncio
import gc
import logging
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from septum_audit import AuditRecord, JsonlFileSink, RetentionPolicy
from septum_audit import sink as sink_module
from septum_audit import worker as worker_module
from septum_audit.config import AuditConfig
from septum_audit.events import LedgerIntegrityError
from septum_audit.retention import RewriteResult

from _helpers import DeadQueue, IdleQueue, put, run_worker_until


@pytest.fixture(autouse=True)
def _no_status_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the fake sinks (which point at /var/log) away from the real filesystem."""
    monkeypatch.setattr(worker_module, "_report_status", lambda *args, **kwargs: None)


class _ScriptedSink:
    """Stands in for a sink; runs pop outcomes (int = removed, Exception = raise), then return 0."""

    path = "/var/log/septum/audit.jsonl"

    def __init__(self, outcomes: list[int | Exception]) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    async def apply_retention_outcome(self, policy: RetentionPolicy) -> RewriteResult:
        self.calls += 1
        outcome = self._outcomes.pop(0) if self._outcomes else 0
        if isinstance(outcome, Exception):
            raise outcome
        return RewriteResult(pruned=outcome, torn=0, head=None)


async def _run_until_calls(sink: _ScriptedSink, calls: int, policy: RetentionPolicy) -> None:
    task = asyncio.create_task(worker_module._retention_loop(sink, policy, interval_seconds=0))
    while sink.calls < calls:
        await asyncio.sleep(0)
    task.cancel()
    await asyncio.wait({task})


async def test_retention_loop_applies_policy_on_every_tick():
    sink = _ScriptedSink([0, 2])
    await _run_until_calls(sink, 3, RetentionPolicy(max_age_days=1))
    assert sink.calls == 3


async def test_failed_retention_run_is_logged_and_retried_next_run(caplog: pytest.LogCaptureFixture):
    sink = _ScriptedSink([OSError("disk full at /secret/path"), 1])
    with caplog.at_level(logging.INFO, logger="septum_audit.worker"):
        await _run_until_calls(sink, 2, RetentionPolicy(max_records=5))

    messages = [r.getMessage() for r in caplog.records if r.name == "septum_audit.worker"]
    failures = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(failures) == 1
    assert "OSError" in failures[0].getMessage()
    assert all("/secret/path" not in m for m in messages)
    assert "audit retention pruned 1 record(s) and dropped 0 torn line(s)" in messages


async def test_failure_in_real_sink_is_logged_and_next_run_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    sink = JsonlFileSink(tmp_path / "audit.jsonl")
    await put(sink, AuditRecord(id="old", timestamp=1.0, source="s", event_type="e"))
    real_rewrite = sink_module.rewrite_jsonl_locked
    attempts: list[int] = []

    def _fail_first(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("transient")
        return real_rewrite(*args, **kwargs)

    monkeypatch.setattr(sink_module, "rewrite_jsonl_locked", _fail_first)

    with caplog.at_level(logging.ERROR, logger="septum_audit.worker"):
        task = asyncio.create_task(
            worker_module._retention_loop(sink, RetentionPolicy(max_age_days=1), interval_seconds=0)
        )
        for _ in range(1000):
            if len(attempts) >= 2 and not list(sink.read_all()):
                break
            await asyncio.sleep(0)
        task.cancel()
        await asyncio.wait({task})

    assert len(attempts) == 2
    assert list(sink.read_all()) == []
    assert any("audit retention run failed" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(("pass_seconds", "expected_sleep"), [(5.0, 3595.0), (500.0, 4500.0)])
async def test_next_pass_starts_one_period_after_the_previous_started(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pass_seconds: float, expected_sleep: float
):
    clock = [1000.0]
    sleeps: list[float] = []
    reported: list[tuple[str, int]] = []
    passes: list[int] = []

    class _Sink:
        path = tmp_path / "audit.jsonl"

        async def apply_retention_outcome(self, policy):
            passes.append(1)
            clock[0] += pass_seconds
            return RewriteResult(0, 0, None)

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        raise asyncio.CancelledError

    async def _inline(func, *args):
        return func(*args)

    monkeypatch.setattr(worker_module, "time", SimpleNamespace(monotonic=lambda: clock[0], time=time.time))
    monkeypatch.setattr(worker_module, "asyncio", SimpleNamespace(sleep=_fake_sleep, to_thread=_inline))
    monkeypatch.setattr(
        worker_module, "_report_status",
        lambda ledger, state, last, failures, interval: reported.append((state, interval)),
    )

    with pytest.raises(asyncio.CancelledError):
        await worker_module._retention_loop(_Sink(), RetentionPolicy(max_records=3), 3600)

    assert passes == [1]
    assert sleeps == [pytest.approx(expected_sleep)]
    assert reported == [("ok", max(3600, int(pass_seconds * 10)))]


def test_adaptive_delay_is_at_least_ten_times_the_last_pass():
    assert worker_module._next_delay(3600, 10.0) == 3600.0
    assert worker_module._next_delay(3600, 1000.0) == 10_000.0


async def test_status_interval_is_the_delay_rounded_up(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    reported: list[int] = []

    class _Stop(Exception):
        pass

    def _record(ledger, state, last_success_at, failures, interval):
        reported.append(interval)

    async def _sleep(_seconds):
        raise _Stop

    async def _inline(function, *args):
        return function(*args)

    class _Sink:
        path = str(tmp_path / "audit.jsonl")

        async def apply_retention_outcome(self, policy):
            return SimpleNamespace(pruned=0, torn=0)

    monkeypatch.setattr(worker_module, "_report_status", _record)
    monkeypatch.setattr(worker_module, "_next_delay", lambda _interval, _elapsed: 2.1)
    monkeypatch.setattr(worker_module, "asyncio", SimpleNamespace(sleep=_sleep, to_thread=_inline))

    with pytest.raises(_Stop):
        await worker_module._retention_loop(_Sink(), RetentionPolicy(max_age_days=1), interval_seconds=1)

    assert reported == [3]


async def test_consecutive_failures_count_up_and_reset_on_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    reported: list[tuple[str, int]] = []

    class _Stop(Exception):
        pass

    def _record(ledger, state, last_success_at, failures, interval):
        reported.append((state, failures))

    sleeps = 0

    async def _sleep(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps == 4:
            raise _Stop

    async def _inline(function, *args):
        return function(*args)

    outcomes: list[object] = [OSError("x"), LedgerIntegrityError("y"), SimpleNamespace(pruned=0, torn=0), OSError("z")]

    class _Sink:
        path = str(tmp_path / "audit.jsonl")

        async def apply_retention_outcome(self, policy):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    monkeypatch.setattr(worker_module, "_report_status", _record)
    monkeypatch.setattr(worker_module, "asyncio", SimpleNamespace(sleep=_sleep, to_thread=_inline))

    with pytest.raises(_Stop):
        await worker_module._retention_loop(_Sink(), RetentionPolicy(max_age_days=1), interval_seconds=1)

    assert reported == [("failed", 1), ("halted", 2), ("ok", 0), ("failed", 1)]


async def test_integrity_error_is_logged_as_halt_on_every_tick(caplog: pytest.LogCaptureFixture):
    class _BrokenSink:
        path = Path("/var/log/septum/audit.jsonl")

        def __init__(self) -> None:
            self.calls = 0

        async def apply_retention_outcome(self, policy: RetentionPolicy):
            self.calls += 1
            raise LedgerIntegrityError("ledger chain broken at line 3; retention halted until the ledger is repaired")

    sink = _BrokenSink()
    with caplog.at_level(logging.ERROR, logger="septum_audit.worker"):
        task = asyncio.create_task(worker_module._retention_loop(sink, RetentionPolicy(max_age_days=1), interval_seconds=0))
        while sink.calls < 2:
            await asyncio.sleep(0)
        task.cancel()
        await asyncio.wait({task})

    halts = [r.getMessage() for r in caplog.records if "retention halted" in r.getMessage()]
    assert len(halts) >= 2
    assert all("line 3" in message for message in halts)
    assert all("failed" not in message for message in halts)


async def test_dead_consumer_makes_the_worker_exit_with_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    monkeypatch.setattr(worker_module, "backend_from_env", lambda _topic: DeadQueue())
    config = AuditConfig(sink_path=str(tmp_path / "audit.jsonl"))

    with caplog.at_level(logging.ERROR, logger="septum_audit.worker"):
        with pytest.raises(RuntimeError, match="consumer task stopped unexpectedly"):
            await asyncio.wait_for(worker_module._run(config), timeout=10)

    messages = [r.getMessage() for r in caplog.records if r.name == "septum_audit.worker"]
    assert any("consumer task stopped unexpectedly" in m and "RuntimeError" in m for m in messages)


async def test_dead_consumer_is_reported_ahead_of_a_dead_retention_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    async def _dies(*_args, **_kwargs):
        raise RuntimeError("retention died")

    monkeypatch.setattr(worker_module, "backend_from_env", lambda _topic: DeadQueue())
    monkeypatch.setattr(worker_module, "_retention_loop", _dies)
    config = AuditConfig(sink_path=str(tmp_path / "audit.jsonl"), retention_max_age_days=1)

    with pytest.raises(RuntimeError, match="consumer task stopped unexpectedly"):
        await asyncio.wait_for(worker_module._run(config), timeout=10)


async def test_dead_retention_task_makes_the_worker_exit_with_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    async def _dies(*_args, **_kwargs):
        raise RuntimeError("retention died")

    monkeypatch.setattr(worker_module, "backend_from_env", lambda _topic: IdleQueue())
    monkeypatch.setattr(worker_module, "_retention_loop", _dies)
    config = AuditConfig(sink_path=str(tmp_path / "audit.jsonl"), retention_max_age_days=1)

    with pytest.raises(RuntimeError, match="retention task stopped unexpectedly"):
        await asyncio.wait_for(worker_module._run(config), timeout=10)


async def test_worker_waits_for_its_tasks_to_unwind_before_closing_the_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    order: list[str] = []

    class _SlowUnwindQueue:
        async def consume(self, **_kwargs):
            try:
                await asyncio.Event().wait()
                yield  # pragma: no cover
            finally:
                await asyncio.sleep(0.05)
                order.append("consumer-unwound")

        async def close(self) -> None:
            order.append("queue-closed")

    async def _dies(*_args, **_kwargs):
        raise RuntimeError("retention died")

    monkeypatch.setattr(worker_module, "backend_from_env", lambda _topic: _SlowUnwindQueue())
    monkeypatch.setattr(worker_module, "_retention_loop", _dies)
    config = AuditConfig(sink_path=str(tmp_path / "audit.jsonl"), retention_max_age_days=1)

    with pytest.raises(RuntimeError):
        await asyncio.wait_for(worker_module._run(config), timeout=10)

    assert order == ["consumer-unwound", "queue-closed"]


async def test_error_in_the_other_task_during_shutdown_is_logged_not_left_unretrieved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    async def _fails_on_cancel(*_args, **_kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise ValueError("cleanup failed") from None

    loop_errors: list[dict] = []
    asyncio.get_running_loop().set_exception_handler(lambda _loop, context: loop_errors.append(context))
    monkeypatch.setattr(worker_module, "backend_from_env", lambda _topic: DeadQueue())
    monkeypatch.setattr(worker_module, "_retention_loop", _fails_on_cancel)
    config = AuditConfig(sink_path=str(tmp_path / "audit.jsonl"), retention_max_age_days=1)

    with caplog.at_level("ERROR", logger="septum_audit.worker"):
        with pytest.raises(RuntimeError, match="consumer task stopped unexpectedly"):
            await asyncio.wait_for(worker_module._run(config), timeout=10)
        gc.collect()

    messages = [r.getMessage() for r in caplog.records if r.name == "septum_audit.worker"]
    assert any("during shutdown" in m and "ValueError" in m for m in messages)
    assert not [c for c in loop_errors if "never retrieved" in str(c.get("message", ""))]


async def test_worker_gives_the_retention_policy_the_exact_age_cutoff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    seen: list[RetentionPolicy] = []

    async def _spy(sink, policy, interval):
        seen.append(policy)
        await asyncio.Event().wait()

    monkeypatch.setattr(worker_module, "_retention_loop", _spy)
    config = AuditConfig(
        sink_path=str(tmp_path / "audit.jsonl"),
        retention_max_age_days=7,
        retention_interval_seconds=900,
    )
    await run_worker_until(config, monkeypatch, lambda: bool(seen))

    assert seen[0].max_age_days == 7
    assert seen[0].cutoff_timestamp(now=1_000_000.0) == 1_000_000.0 - 7 * 86400
