from __future__ import annotations

"""Tamper-evidence regression tests for the hash-chained audit ledger."""

import json
from pathlib import Path

import pytest

from septum_audit import (
    AuditRecord,
    JsonlFileSink,
    MemorySink,
    SplunkHecExporter,
    verify_chain,
)


@pytest.mark.asyncio
async def test_jsonl_sink_writes_chain_links_records(tmp_path: Path) -> None:
    sink = JsonlFileSink(tmp_path / "audit.jsonl")
    a = AuditRecord(id="a", source="api", event_type="x", attributes={"i": 1})
    b = AuditRecord(id="b", source="api", event_type="y", attributes={"i": 2})
    c = AuditRecord(id="c", source="api", event_type="z", attributes={"i": 3})
    for record in (a, b, c):
        await sink.write(record)

    records = list(sink.read_all())
    assert [r.id for r in records] == ["a", "b", "c"]
    verify_chain(records)


@pytest.mark.asyncio
async def test_jsonl_sink_files_are_mode_0o640(tmp_path: Path) -> None:
    sink = JsonlFileSink(tmp_path / "audit.jsonl")
    await sink.write(AuditRecord(id="x", source="api", event_type="x"))
    mode = oct((tmp_path / "audit.jsonl").stat().st_mode & 0o777)
    assert mode == "0o640"


@pytest.mark.asyncio
async def test_post_write_edit_breaks_verify_chain(tmp_path: Path) -> None:
    """An on-disk tamper (someone edits a row's attributes) must be detected."""
    path = tmp_path / "audit.jsonl"
    sink = JsonlFileSink(path)
    await sink.write(AuditRecord(id="a", source="api", event_type="x", attributes={"v": 1}))
    await sink.write(AuditRecord(id="b", source="api", event_type="y", attributes={"v": 2}))

    # Operator (or attacker) edits the second record's attributes
    # without recomputing hash. read_all surfaces the original hash;
    # verify_chain must reject the row.
    lines = path.read_text(encoding="utf-8").splitlines()
    second = json.loads(lines[1])
    second["attributes"] = {"v": "TAMPERED"}
    lines[1] = json.dumps(second, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    records = list(JsonlFileSink(path).read_all())
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_chain(records)


@pytest.mark.asyncio
async def test_record_deletion_breaks_chain(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    sink = JsonlFileSink(path)
    for i in range(3):
        await sink.write(AuditRecord(id=f"r{i}", source="api", event_type="x"))

    lines = path.read_text(encoding="utf-8").splitlines()
    # Delete the middle record — the third record's prev_hash will
    # no longer match the first record's hash.
    del lines[1]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    records = list(JsonlFileSink(path).read_all())
    with pytest.raises(ValueError, match="prev_hash mismatch"):
        verify_chain(records)


def test_memory_sink_chain_is_consistent() -> None:
    sink = MemorySink(initial_records=[
        AuditRecord(id="a", source="api", event_type="x", attributes={"i": 1}),
        AuditRecord(id="b", source="api", event_type="y", attributes={"i": 2}),
    ])
    records = list(sink.read_all())
    verify_chain(records)


def test_splunk_hec_rejects_newline_in_host() -> None:
    """Newline injection into HEC metadata can forge a second event."""
    with pytest.raises(ValueError, match="control character"):
        SplunkHecExporter(host="legit\nfoo")
