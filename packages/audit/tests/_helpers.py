"""Shared builders for the septum-audit tests: chained records, ledger files, legacy lines and fake queues."""

from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path
from typing import Any, Callable, Iterable

from septum_audit import AuditRecord, JsonlFileSink
from septum_audit.events import GENESIS_PREV_HASH

NOW = 1_700_000_000.0
DAY = 86400.0


class Blocked(Exception):
    """Raised when an operation that must fail fast does not return within its bound."""


def chained(record_id: str, prev: str, timestamp: float = NOW, received_at: float | None = None) -> AuditRecord:
    return AuditRecord(
        id=record_id, timestamp=timestamp, source="s", event_type="e", received_at=received_at
    ).with_hash_chain(prev_hash=prev)


def chain(count: int, timestamp: float = NOW, prev: str = GENESIS_PREV_HASH) -> list[AuditRecord]:
    records: list[AuditRecord] = []
    for index in range(count):
        record = AuditRecord(
            id=f"r-{index}", timestamp=timestamp, source="s", event_type="e", received_at=timestamp
        ).with_hash_chain(prev_hash=prev)
        records.append(record)
        prev = record.hash
    return records


def lines(records: Iterable[AuditRecord]) -> str:
    return "".join(record.to_json() + "\n" for record in records)


def write_lines(path: Path, records: Iterable[AuditRecord]) -> None:
    path.write_text(lines(records), encoding="utf-8")


def strip_chain(record: AuditRecord) -> str:
    payload = json.loads(record.to_json())
    for key in ("prev_hash", "hash", "received_at"):
        del payload[key]
    return json.dumps(payload, separators=(",", ":"))


def ids(path: Path) -> list[str]:
    return [json.loads(line)["id"] for line in path.read_text(encoding="utf-8").split("\n") if line]


async def put(sink: JsonlFileSink, record: AuditRecord) -> None:
    """Write through a fresh sink stamped with the record's own timestamp, so age tests control received_at."""
    await JsonlFileSink(sink.path, clock=lambda: record.timestamp).write(record)


async def write_at(path: Path, record: AuditRecord, received_at: float) -> None:
    await JsonlFileSink(path, clock=lambda: received_at).write(record)


async def append(sink: JsonlFileSink, name: str) -> None:
    await sink.write(AuditRecord(id=name, source="s", event_type="e"))


async def seed(path: Path, ages_in_days: list[float]) -> list[AuditRecord]:
    sink = JsonlFileSink(path)
    for index, age in enumerate(ages_in_days):
        await put(
            sink,
            AuditRecord(
                id=f"r-{index}", timestamp=NOW - age * DAY, source="s", event_type="e", attributes={"index": index}
            ),
        )
    return list(sink.read_all())


async def ledger_with_tail(tmp_path: Path, tail: str) -> tuple[Path, JsonlFileSink]:
    """A ledger holding one record, followed by raw ``tail`` bytes with no newline."""
    target = tmp_path / "audit.jsonl"
    sink = JsonlFileSink(target)
    await put(sink, AuditRecord(id="first", source="s", event_type="e"))
    with target.open("a", encoding="utf-8") as fh:
        fh.write(tail)
    return target, sink


def ledger_from_text(tmp_path: Path, text: str) -> tuple[Path, JsonlFileSink]:
    target = tmp_path / "audit.jsonl"
    target.write_text(text, encoding="utf-8")
    return target, JsonlFileSink(target)


def legacy_line(record_id: str, timestamp_literal: str, attributes: str = "{}") -> str:
    """A hash-less ledger line written by an older serialiser; the timestamp is a raw JSON literal."""
    return (
        f'{{"id":"{record_id}","timestamp":{timestamp_literal},"source":"s","event_type":"e",'
        f'"attributes":{attributes}}}'
    )


def legacy_ledger(path: Path) -> None:
    """Hash-less lines whose timestamps and attributes are non-finite literals."""
    lines_ = [
        legacy_line("nan_ts", "NaN"),
        legacy_line("inf_ts", "Infinity", '{"n":-Infinity}'),
        legacy_line("nan_attr", f"{NOW}", '{"score":NaN,"list":[Infinity]}'),
    ]
    path.write_text("\n".join(lines_) + "\n", encoding="utf-8")


def strict_load(line: str) -> dict[str, Any]:
    def _reject(constant: str):
        raise ValueError(f"non-strict JSON constant {constant}")

    return json.loads(line, parse_constant=_reject)


def deep(levels: int) -> object:
    value: object = "leaf"
    for _ in range(levels):
        value = {"n": value}
    return value


class FakeMessage:
    def __init__(self, payload: Any, message_id: str = "m-1") -> None:
        self.id = message_id
        self.payload = payload


class FakeQueue:
    """Hands out each message once, in order; records acks and nacks."""

    def __init__(self, messages: Iterable[FakeMessage] = ()) -> None:
        self._messages = list(messages)
        self.acked: list[str] = []
        self.nacked: list[tuple[str, bool]] = []

    async def consume(self, **_kwargs):
        while self._messages:
            yield self._messages.pop(0)

    async def ack(self, message_id: str) -> None:
        self.acked.append(message_id)

    async def nack(self, message_id: str, *, requeue: bool) -> None:
        self.nacked.append((message_id, requeue))

    async def close(self) -> None:
        return None


class CaptureSink:
    """Keeps every record it is given, or fails every write with ``error``."""

    def __init__(self, error: Exception | None = None) -> None:
        self.records: list[AuditRecord] = []
        self._error = error

    async def write(self, record: AuditRecord) -> None:
        if self._error is not None:
            raise self._error
        self.records.append(record)

    def read_all(self):
        return iter(self.records)

    async def close(self) -> None:
        return None


class DeadQueue:
    """A consumer whose connection is already lost."""

    async def consume(self, **_kwargs):
        raise RuntimeError("queue connection lost")
        yield  # pragma: no cover

    async def close(self) -> None:
        return None


class IdleQueue:
    """A consumer that never receives anything."""

    async def consume(self, **_kwargs):
        await asyncio.Event().wait()
        yield  # pragma: no cover

    async def close(self) -> None:
        return None


async def poll(predicate: Callable[[], bool], *, seconds: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


async def run_worker_until(config, monkeypatch, ready: Callable[[], bool]) -> None:
    """Start the worker on an idle queue, wait for ``ready``, then deliver SIGTERM and wait for exit."""
    from septum_audit import worker as worker_module

    monkeypatch.setattr(worker_module, "backend_from_env", lambda _topic: IdleQueue())
    task = asyncio.create_task(worker_module._run(config))
    for _ in range(1000):
        if ready():
            break
        await asyncio.sleep(0.005)
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(task, timeout=10)


def _blocked(_signum, _frame):
    raise Blocked("operation did not return; it should have failed fast")


class bounded:
    """Context manager that turns a blocking call into ``Blocked`` after ``seconds``."""

    def __init__(self, seconds: int = 5) -> None:
        self._seconds = seconds
        self._previous = None

    def __enter__(self) -> None:
        self._previous = signal.signal(signal.SIGALRM, _blocked)
        signal.alarm(self._seconds)

    def __exit__(self, *_exc) -> None:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, self._previous)
