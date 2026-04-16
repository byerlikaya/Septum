"""MemorySink and JsonlFileSink behavior, including concurrent writes."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from septum_audit import AuditRecord, JsonlFileSink, MemorySink


async def test_memory_sink_appends_and_iterates():
    sink = MemorySink()
    await sink.write(AuditRecord(source="api", event_type="a"))
    await sink.write(AuditRecord(source="api", event_type="b"))
    records = list(sink.read_all())
    assert [r.event_type for r in records] == ["a", "b"]
    assert len(sink) == 2


async def test_memory_sink_initial_records_seed_the_sink():
    seed = [
        AuditRecord(source="api", event_type="a"),
        AuditRecord(source="api", event_type="b"),
    ]
    sink = MemorySink(initial_records=seed)
    assert len(sink) == 2
    assert [r.event_type for r in sink.read_all()] == ["a", "b"]


async def test_memory_sink_snapshot_iteration_safe_under_concurrent_writes():
    sink = MemorySink()
    await sink.write(AuditRecord(source="api", event_type="a"))
    iterator = sink.read_all()
    await sink.write(AuditRecord(source="api", event_type="b"))
    consumed = list(iterator)
    assert len(consumed) == 1


async def test_jsonl_sink_writes_one_record_per_line(tmp_path: Path):
    sink = JsonlFileSink(tmp_path / "log" / "audit.jsonl")
    await sink.write(AuditRecord(source="api", event_type="alpha"))
    await sink.write(AuditRecord(source="api", event_type="beta"))

    raw = sink.path.read_text(encoding="utf-8")
    lines = raw.strip().split("\n")
    assert len(lines) == 2
    payloads = [json.loads(line) for line in lines]
    assert [p["event_type"] for p in payloads] == ["alpha", "beta"]


async def test_jsonl_sink_creates_parent_directory(tmp_path: Path):
    sink = JsonlFileSink(tmp_path / "deep" / "nested" / "audit.jsonl")
    await sink.write(AuditRecord(source="api", event_type="x"))
    assert sink.path.exists()


async def test_jsonl_sink_read_all_skips_blank_and_corrupt_lines(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    target.write_text(
        '{"id":"a","timestamp":1,"source":"s","event_type":"e","attributes":{}}\n'
        "\n"
        "not-json\n"
        '{"id":"b","timestamp":2,"source":"s","event_type":"e","attributes":{}}\n',
        encoding="utf-8",
    )
    sink = JsonlFileSink(target)
    ids = [r.id for r in sink.read_all()]
    assert ids == ["a", "b"]


async def test_jsonl_sink_read_all_handles_missing_file(tmp_path: Path):
    sink = JsonlFileSink(tmp_path / "never_written.jsonl")
    assert list(sink.read_all()) == []


async def test_jsonl_sink_concurrent_writes_serialize(tmp_path: Path):
    sink = JsonlFileSink(tmp_path / "audit.jsonl")
    await asyncio.gather(
        *[
            sink.write(
                AuditRecord(source="api", event_type=f"evt-{i}")
            )
            for i in range(20)
        ]
    )
    lines = sink.path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 20
    decoded = [json.loads(line) for line in lines]
    assert {d["event_type"] for d in decoded} == {f"evt-{i}" for i in range(20)}


async def test_close_is_safe_to_call_twice(tmp_path: Path):
    sink = JsonlFileSink(tmp_path / "audit.jsonl")
    await sink.close()
    await sink.close()
