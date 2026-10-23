"""Retention status sidecar: what the worker writes, how it is read back, how stale or blocked states map to /health, and the disabled heartbeat."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import pytest

from septum_audit import AuditRecord, JsonlFileSink, RetentionPolicy
from septum_audit import retention as retention_module
from septum_audit import worker as worker_module
from septum_audit.config import AuditConfig
from septum_audit.main import _retention_state
from septum_audit.status import RetentionStatus, read_status, status_path, write_status

from _helpers import NOW, chain, poll, run_worker_until

_EMPTY = RetentionStatus("", None, 0, 0.0, 1)


def _status(**overrides) -> RetentionStatus:
    base = dict(state="ok", last_success_at=NOW, consecutive_failures=0, checked_at=NOW, interval_seconds=60)
    base.update(overrides)
    return RetentionStatus(**base)


async def _run_one_tick(target: Path, sink: JsonlFileSink, *, interval: int = 0) -> None:
    task = asyncio.create_task(worker_module._retention_loop(sink, RetentionPolicy(max_age_days=1), interval_seconds=interval))
    await poll(lambda: read_status(target) is not None)
    task.cancel()
    await asyncio.wait({task})


def test_status_sidecar_is_content_free_and_health_reflects_a_halt(tmp_path: Path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from septum_audit.main import create_app

    target = tmp_path / "audit.jsonl"
    sink = JsonlFileSink(target)
    asyncio.run(sink.write(AuditRecord(id="secret-id", source="s", event_type="e")))
    client = TestClient(create_app(AuditConfig(sink_path=str(target)), sink=JsonlFileSink(target)))

    assert client.get("/health").json()["retention"] == "not_reported"
    assert client.get("/health").status_code == 200

    write_status(target, RetentionStatus(state="halted", last_success_at=None, consecutive_failures=2,
                                         checked_at=time.time(), interval_seconds=3600))
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["retention"] == "halted"
    assert "id" not in status_path(target).read_text()

    write_status(target, RetentionStatus(state="failed", last_success_at=NOW, consecutive_failures=1,
                                         checked_at=time.time(), interval_seconds=3600))
    assert client.get("/health").status_code == 503

    write_status(target, RetentionStatus(state="ok", last_success_at=NOW, consecutive_failures=0,
                                         checked_at=time.time(), interval_seconds=3600))
    assert client.get("/health").status_code == 200

    status_path(target).write_text("{not json", encoding="utf-8")
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["retention"] == "unreadable"


def test_health_is_503_while_retention_is_blocked(tmp_path: Path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from septum_audit.main import create_app

    target = tmp_path / "audit.jsonl"
    client = TestClient(create_app(AuditConfig(sink_path=str(target)), sink=JsonlFileSink(target)))
    write_status(target, RetentionStatus("blocked", NOW, 0, time.time(), 3600))

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["retention"] == "blocked"


async def test_worker_tick_records_halt_in_the_status_sidecar(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    old = chain(1, timestamp=1.0)[0]
    target.write_text(old.to_json() + "\nnot-json\n", encoding="utf-8")
    sink = JsonlFileSink(target)

    task = asyncio.create_task(worker_module._retention_loop(sink, RetentionPolicy(max_age_days=1), interval_seconds=0))
    for _ in range(1000):
        status = read_status(target)
        if status is not None and status.state == "halted":
            break
        await asyncio.sleep(0)
    task.cancel()
    await asyncio.wait({task})

    status = read_status(target)
    assert status is not None
    assert status.state == "halted"
    assert status.consecutive_failures >= 1
    assert target.read_text(encoding="utf-8") == old.to_json() + "\nnot-json\n"


async def test_successful_tick_records_ok_and_a_success_time(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    sink = JsonlFileSink(target)
    await sink.write(AuditRecord(id="a", timestamp=NOW, source="s", event_type="e"))
    before = time.time()

    await _run_one_tick(target, sink)

    status = read_status(target)
    assert status is not None and status.state == "ok"
    assert status.last_success_at is not None and status.last_success_at >= before
    assert status.consecutive_failures == 0


async def test_forged_future_legacy_line_on_an_unanchored_ledger_reports_blocked(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    future = time.time() + 3600
    forged = {"id": "forged", "timestamp": future, "source": "s", "event_type": "e", "attributes": {}}
    kept = chain(1)[0]
    target.write_text(json.dumps(forged) + "\n" + kept.to_json() + "\n", encoding="utf-8")
    before = target.read_text(encoding="utf-8")

    task = asyncio.create_task(
        worker_module._retention_loop(JsonlFileSink(target), RetentionPolicy(max_age_days=1), interval_seconds=3600)
    )
    await poll(lambda: (read_status(target) or _EMPTY).state == "blocked")
    task.cancel()
    await asyncio.wait({task})

    assert target.read_text(encoding="utf-8") == before
    assert _retention_state(target, time.time() - 10, 3600, time.time()) == ("blocked", False)


async def test_blocked_is_not_a_failure_and_keeps_the_last_success_time(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    future = {"id": "f", "timestamp": time.time() + 7200, "source": "s", "event_type": "e", "attributes": {}}
    target.write_text(json.dumps(future) + "\n", encoding="utf-8")

    await _run_one_tick(target, JsonlFileSink(target), interval=3600)

    status = read_status(target)
    assert status.state == "blocked"
    assert status.consecutive_failures == 0
    assert status.last_success_at is not None


async def test_record_stamped_only_slightly_ahead_is_not_blocked(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    near = {"id": "n", "timestamp": time.time() + 60, "source": "s", "event_type": "e", "attributes": {}}
    target.write_text(json.dumps(near) + "\n", encoding="utf-8")

    await _run_one_tick(target, JsonlFileSink(target), interval=3600)

    assert read_status(target).state == "ok"


async def test_legacy_non_finite_timestamp_line_is_kept_and_does_not_block(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    target = tmp_path / "audit.jsonl"
    target.write_text('{"id":"legacy","timestamp":NaN,"source":"s","event_type":"e","attributes":{}}\n', encoding="utf-8")
    before = target.read_text(encoding="utf-8")

    with caplog.at_level("ERROR", logger="septum_audit.worker"):
        await _run_one_tick(target, JsonlFileSink(target), interval=3600)

    assert read_status(target).state == "ok"
    assert target.read_text(encoding="utf-8") == before
    assert not any("retention blocked" in r.getMessage() for r in caplog.records)


async def test_count_only_policy_never_reports_blocked(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    future = time.time() + 10 * 86400
    target.write_text(
        '{"id":"future","timestamp":' + str(future) + ',"source":"s","event_type":"e","attributes":{}}\n',
        encoding="utf-8",
    )

    task = asyncio.create_task(
        worker_module._retention_loop(JsonlFileSink(target), RetentionPolicy(max_records=10), interval_seconds=3600)
    )
    await poll(lambda: read_status(target) is not None)
    task.cancel()
    await asyncio.wait({task})

    assert read_status(target).state == "ok"


def test_pin_is_reported_only_past_the_threshold(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    target.write_text(
        json.dumps({"id": "a", "timestamp": NOW + 200, "source": "s", "event_type": "e", "attributes": {}}) + "\n",
        encoding="utf-8",
    )
    assert retention_module.pin_ahead_seconds(target, NOW) is None
    assert retention_module.pin_ahead_seconds(target, NOW - 400) == pytest.approx(600)


def test_write_status_refuses_an_unknown_state(tmp_path: Path):
    with pytest.raises(ValueError, match="unknown retention state"):
        write_status(tmp_path / "audit.jsonl", _status(state="sleeping"))
    assert not status_path(tmp_path / "audit.jsonl").exists()


def test_status_file_with_an_unknown_state_is_unreadable(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    status_path(target).write_text(
        '{"state":"bogus","last_success_at":null,"consecutive_failures":0}', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="malformed"):
        read_status(target)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("state", '"sleeping"'),
        ("last_success_at", "true"),
        ("checked_at", "true"),
        ("consecutive_failures", "-1"),
        ("consecutive_failures", "true"),
        ("interval_seconds", "-5"),
        ("checked_at", "10" + "0" * 400),
        ("checked_at", "NaN"),
        ("checked_at", "Infinity"),
        ("checked_at", "-Infinity"),
    ],
)
def test_read_status_rejects_invalid_fields(tmp_path: Path, field: str, value: str):
    target = tmp_path / "audit.jsonl"
    body = {"state": '"ok"', "last_success_at": "1.0", "consecutive_failures": "0",
            "checked_at": "1.0", "interval_seconds": "60"}
    body[field] = value
    status_path(target).write_text("{" + ", ".join(f'"{k}": {v}' for k, v in body.items()) + "}", encoding="utf-8")

    with pytest.raises(ValueError, match="malformed"):
        read_status(target)


def test_os_error_reading_the_status_is_unreadable_not_a_500(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from septum_audit import main as main_module

    def _broken(_path):
        raise OSError("input/output error")

    monkeypatch.setattr(main_module, "read_status", _broken)
    assert main_module._retention_state(tmp_path / "audit.jsonl", NOW, 3600, NOW) == ("unreadable", False)


@pytest.mark.parametrize("interval", [60, 3600])
def test_status_is_stale_only_after_two_intervals_plus_the_grace_period(tmp_path: Path, interval: int):
    target = tmp_path / "audit.jsonl"
    write_status(target, _status(checked_at=NOW, interval_seconds=interval))
    limit = NOW + 2 * interval + 300

    assert _retention_state(target, NOW, interval, limit) == ("ok", True)
    assert _retention_state(target, NOW, interval, limit + 1) == ("stale", False)


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (60, ("not_reported", True)),
        (3600 + 600, ("not_reported", True)),
        (2 * 3600 + 300, ("not_reported", True)),
        (2 * 3600 + 301, ("missing", False)),
    ],
    ids=["first-minute", "one-interval-plus-grace", "two-intervals-plus-grace", "past-grace"],
)
def test_missing_status_is_not_reported_within_the_grace_then_missing(tmp_path: Path, offset: int, expected):
    target = tmp_path / "audit.jsonl"
    assert _retention_state(target, NOW, 3600, NOW + offset) == expected


def test_status_write_failure_is_logged_and_the_next_tick_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    target = tmp_path / "audit.jsonl"
    real_write = worker_module.write_status

    def _fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(worker_module, "write_status", _fail)
    with caplog.at_level(logging.ERROR, logger="septum_audit.worker"):
        worker_module._report_status(target, "ok", None, 0, 3600)
    assert any("could not be written" in r.getMessage() for r in caplog.records)
    assert read_status(target) is None

    monkeypatch.setattr(worker_module, "write_status", real_write)
    worker_module._report_status(target, "ok", None, 0, 3600)
    assert read_status(target).state == "ok"


async def test_disabled_worker_heartbeat_keeps_the_status_fresh(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    task = asyncio.create_task(worker_module._disabled_loop(target, 3600))
    await poll(lambda: read_status(target) is not None)
    task.cancel()
    await asyncio.wait({task})

    status = read_status(target)
    assert status.state == "disabled"
    assert abs(status.checked_at - time.time()) < 60


async def test_worker_without_retention_runs_the_disabled_heartbeat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / "audit.jsonl"
    config = AuditConfig(sink_path=str(target), retention_interval_seconds=900)
    await run_worker_until(config, monkeypatch, lambda: read_status(target) is not None)

    status = read_status(target)
    assert status.state == "disabled"
    assert status.interval_seconds == 900
