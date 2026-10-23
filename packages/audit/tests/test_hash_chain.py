"""Tamper evidence for the hash-chained audit ledger: chain links, hash validity, verification and export rejection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from septum_audit import AuditRecord, JsonlFileSink, MemorySink, SplunkHecExporter, verify_chain
from septum_audit.anchor import is_hash
from septum_audit.events import GENESIS_PREV_HASH


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
    del lines[1]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    records = list(JsonlFileSink(path).read_all())
    with pytest.raises(ValueError, match="prev_hash mismatch"):
        verify_chain(records)


@pytest.mark.asyncio
async def test_memory_sink_chain_is_consistent() -> None:
    sink = MemorySink(initial_records=[
        AuditRecord(id="a", source="api", event_type="x", attributes={"i": 1}),
        AuditRecord(id="b", source="api", event_type="y", attributes={"i": 2}),
    ])
    verify_chain(list(sink.read_all()))


def test_splunk_hec_rejects_newline_in_host() -> None:
    """Newline injection into HEC metadata can forge a second event."""
    with pytest.raises(ValueError, match="control character"):
        SplunkHecExporter(host="legit\nfoo")


@pytest.mark.parametrize(
    "value",
    ["a" * 64, "0" * 64, "f" * 64],
)
def test_is_hash_accepts_exactly_64_lowercase_hex(value: str) -> None:
    assert is_hash(value)


@pytest.mark.parametrize(
    "value",
    ["a" * 65, "A" * 64, "g" * 64, "a" * 32 + "\n" + "a" * 31, " " + "a" * 63, "a" * 63, "", None, 5, b"a" * 64,
     "a" * 64 + "\n"],
)
def test_is_hash_rejects_everything_but_exactly_64_lowercase_hex(value) -> None:
    assert not is_hash(value)


def test_verify_chain_accepts_only_listed_first_links() -> None:
    first = AuditRecord(id="a", timestamp=1_700_000_000.0, source="s", event_type="e").with_hash_chain(
        prev_hash="1" * 64
    )
    with pytest.raises(ValueError):
        verify_chain([first])
    verify_chain([first], accepted_first_prev=("1" * 64,))


def test_verify_chain_cuts_a_producer_id_to_64_characters() -> None:
    import dataclasses

    record = AuditRecord(id="y" * 200, timestamp=1_700_000_000.0, source="s", event_type="e").with_hash_chain(
        prev_hash=GENESIS_PREV_HASH
    )
    forged = dataclasses.replace(record, hash="0" * 64)

    with pytest.raises(ValueError) as excinfo:
        verify_chain([forged])
    assert "y" * 64 in str(excinfo.value)
    assert "y" * 65 not in str(excinfo.value)
