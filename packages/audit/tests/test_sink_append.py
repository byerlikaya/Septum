"""Appends to the JSONL ledger: rollback on failure, received_at stamping, ingest-time replacement and the cached chain head."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from septum_audit import AuditRecord, JsonlFileSink, MemorySink, RetentionPolicy
from septum_audit import sink as sink_module
from septum_audit.events import GENESIS_PREV_HASH, INGEST_FLAG, UnstorableRecordError
from septum_audit.sink import _check_reads_back, _tail_hash

from _helpers import NOW, append, chain, chained, ids, put, write_lines


async def test_failed_fsync_rolls_back_and_the_retry_chains_correctly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / "audit.jsonl"
    sink = JsonlFileSink(target)
    await put(sink, AuditRecord(id="first", source="s", event_type="e"))
    size_before = target.stat().st_size

    original_fsync = os.fsync
    calls: list[int] = []

    def _fail_once(fd):
        calls.append(fd)
        if len(calls) == 1:
            raise OSError("simulated fsync failure")
        return original_fsync(fd)

    monkeypatch.setattr(os, "fsync", _fail_once)
    with pytest.raises(OSError, match="simulated fsync failure"):
        await put(sink, AuditRecord(id="second", source="s", event_type="e"))
    monkeypatch.undo()

    assert target.stat().st_size == size_before
    await put(sink, AuditRecord(id="second", source="s", event_type="e"))

    assert ids(target) == ["first", "second"]
    sink.verify()


async def test_failed_close_after_a_durable_write_clears_the_cached_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / "audit.jsonl"
    sink = JsonlFileSink(target)
    await sink.write(AuditRecord(id="first", source="s", event_type="e"))
    real_close = os.close
    closes: list[int] = []

    def _close_fails_once(fd):
        closes.append(fd)
        real_close(fd)
        if len(closes) == 1:
            raise OSError("simulated close failure")

    monkeypatch.setattr(os, "close", _close_fails_once)
    with pytest.raises(OSError, match="simulated close failure"):
        await sink.write(AuditRecord(id="second", source="s", event_type="e"))
    monkeypatch.undo()

    assert sink._cached_last_hash is None
    await sink.write(AuditRecord(id="third", source="s", event_type="e"))
    sink.verify()


async def test_failed_rollback_does_not_mask_the_original_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    sink = JsonlFileSink(tmp_path / "audit.jsonl")

    def _fsync_fails(fd):
        raise OSError("original fsync failure")

    def _truncate_fails(fd, size):
        raise OSError("truncate failure")

    monkeypatch.setattr(os, "fsync", _fsync_fails)
    monkeypatch.setattr(os, "ftruncate", _truncate_fails)
    with pytest.raises(OSError, match="original fsync failure"):
        await put(sink, AuditRecord(id="x", source="s", event_type="e"))


async def test_partial_os_writes_are_completed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / "audit.jsonl"
    sink = JsonlFileSink(target)
    real_write = os.write

    def _five_bytes_at_a_time(fd, data):
        return real_write(fd, bytes(data)[:5])

    monkeypatch.setattr(os, "write", _five_bytes_at_a_time)
    await put(sink, AuditRecord(id="chunky", source="s", event_type="e"))
    monkeypatch.undo()

    assert ids(target) == ["chunky"]
    sink.verify()


async def test_sink_stamps_received_at_and_the_hash_covers_it(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    await JsonlFileSink(target, clock=lambda: 1_700_000_123.5).write(
        AuditRecord(id="a", timestamp=1.0, source="s", event_type="e")
    )

    record = next(JsonlFileSink(target).read_all())
    assert record.received_at == 1_700_000_123.5
    assert record.timestamp == 1.0
    JsonlFileSink(target).verify()


async def test_producer_copy_of_the_ingest_flag_is_dropped_from_a_clean_record(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    sink = JsonlFileSink(target)
    await sink.write(AuditRecord(id="clean", source="s", event_type="e", attributes={INGEST_FLAG: True}))

    record = next(sink.read_all())
    assert INGEST_FLAG not in record.attributes


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
async def test_non_finite_timestamp_is_replaced_by_ingest_time_and_flagged(tmp_path: Path, bad: float):
    sink = JsonlFileSink(tmp_path / "audit.jsonl", clock=lambda: NOW)
    await sink.write(AuditRecord(id="ts", timestamp=bad, source="s", event_type="e"))

    record = next(sink.read_all())
    assert record.timestamp == NOW and record.received_at == NOW
    assert record.attributes[INGEST_FLAG] is True
    sink.verify()


async def test_nested_non_finite_values_are_replaced_at_every_depth(tmp_path: Path):
    sink = JsonlFileSink(tmp_path / "audit.jsonl", clock=lambda: NOW)
    attributes = {"a": {"b": [float("-inf"), {"c": float("nan")}]}, "fine": 1.5, "n": 3}
    await sink.write(AuditRecord(id="deep", timestamp=NOW, source="s", event_type="e", attributes=attributes))

    stored = next(sink.read_all()).attributes
    assert stored["a"] == {"b": ["-Infinity", {"c": "NaN"}]}
    assert stored["fine"] == 1.5 and stored["n"] == 3
    assert stored[INGEST_FLAG] is True
    assert attributes["a"]["b"][0] == float("-inf")
    sink.verify()


async def test_after_a_rewrite_the_cached_head_is_seeded_and_the_next_append_chains(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    sink = JsonlFileSink(target)
    await put(sink, AuditRecord(id="old", timestamp=1.0, source="s", event_type="e"))
    await put(sink, AuditRecord(id="kept", timestamp=NOW, source="s", event_type="e"))
    assert await sink.apply_retention(RetentionPolicy(max_age_days=1), now=NOW) == 1
    kept_hash = list(sink.read_all())[-1].hash

    assert sink._cached_last_hash == kept_hash
    await put(sink, AuditRecord(id="next", timestamp=NOW, source="s", event_type="e"))

    records = list(sink.read_all())
    assert records[-1].prev_hash == kept_hash
    sink.verify()


async def test_head_is_seeded_after_rewrite_even_when_the_sink_had_no_cache(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    writer = JsonlFileSink(target)
    await put(writer, AuditRecord(id="old", timestamp=1.0, source="s", event_type="e"))
    await put(writer, AuditRecord(id="kept", timestamp=NOW, source="s", event_type="e"))
    fresh = JsonlFileSink(target)

    assert await fresh.apply_retention(RetentionPolicy(max_age_days=1), now=NOW) == 1

    assert fresh._cached_last_hash == list(fresh.read_all())[-1].hash


async def test_chain_head_is_read_from_disk_once_per_sink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / "audit.jsonl"
    await append(JsonlFileSink(target), "seed")
    reads = 0
    real_tail = sink_module._tail_hash

    def _counting(fh):
        nonlocal reads
        reads += 1
        return real_tail(fh)

    monkeypatch.setattr(sink_module, "_tail_hash", _counting)
    sink = JsonlFileSink(target)
    for name in ("a", "b", "c"):
        await append(sink, name)

    assert reads == 1
    sink.verify()


async def test_cold_start_head_is_read_from_the_tail_of_a_long_ledger(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    records = chain(200)
    write_lines(target, records)

    assert JsonlFileSink(target)._last_hash() == records[-1].hash


def test_tail_hash_skips_trailing_unparseable_lines(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    records = chain(3)
    target.write_text(records[-1].to_json() + "\n" + "garbage\n\n", encoding="utf-8")

    with target.open("rb") as fh:
        assert _tail_hash(fh) == records[-1].hash


@pytest.mark.parametrize("bad", ["trailing-newline", "short", "uppercase", "overlong"])
def test_tail_hash_ignores_values_that_are_not_exact_hashes(tmp_path: Path, bad: str):
    good = chained("good", GENESIS_PREV_HASH)
    value = {
        "trailing-newline": good.hash + "\n",
        "short": "abc",
        "uppercase": "A" * 64,
        "overlong": "a" * 65,
    }[bad]
    target = tmp_path / "audit.jsonl"
    target.write_text(good.to_json() + "\n" + json.dumps({"id": bad, "hash": value}) + "\n", encoding="utf-8")

    with target.open("rb") as fh:
        assert _tail_hash(fh) == good.hash


async def test_memory_sink_links_each_record_to_its_predecessor():
    sink = MemorySink()
    for name in ("a", "b", "c"):
        await sink.write(AuditRecord(id=name, source="s", event_type="e"))

    records = list(sink.read_all())
    assert records[0].prev_hash == GENESIS_PREV_HASH
    assert records[1].prev_hash == records[0].hash
    assert records[2].prev_hash == records[1].hash


async def test_memory_sink_initial_records_are_chained_and_extended():
    sink = MemorySink([AuditRecord(id="a", source="s", event_type="e"), AuditRecord(id="b", source="s", event_type="e")])
    await sink.write(AuditRecord(id="c", source="s", event_type="e"))

    records = list(sink.read_all())
    assert [r.prev_hash for r in records[1:]] == [records[0].hash, records[1].hash]


@pytest.mark.parametrize(
    "raw", ["0", "-0", "0.0", "1e-400", "1700000000", True, None, 0, 0.0, -0.0, float("inf"), float("nan"), 10**400], ids=repr
)
async def test_a_timestamp_that_is_not_a_finite_non_zero_number_is_replaced_by_ingest_time_and_flagged(tmp_path: Path, raw):
    sink = JsonlFileSink(tmp_path / "audit.jsonl", clock=lambda: NOW)
    await sink.write(AuditRecord.from_dict({"id": "x", "source": "s", "event_type": "e", "timestamp": raw}))

    record = next(sink.read_all())
    assert record.timestamp == NOW
    assert record.attributes[INGEST_FLAG] is True
    sink.verify()


@pytest.mark.parametrize("zero", [0, 0.0, -0.0])
async def test_a_record_built_directly_with_a_zero_timestamp_is_replaced_not_stored_as_zero(tmp_path: Path, zero):
    sink = JsonlFileSink(tmp_path / "audit.jsonl", clock=lambda: NOW)
    await sink.write(AuditRecord(id="x", timestamp=zero, source="s", event_type="e"))

    record = next(sink.read_all())
    assert record.timestamp == NOW
    assert record.attributes[INGEST_FLAG] is True


async def test_a_valid_timestamp_is_kept_and_the_record_is_not_flagged(tmp_path: Path):
    sink = JsonlFileSink(tmp_path / "audit.jsonl", clock=lambda: NOW)
    await sink.write(AuditRecord.from_dict({"id": "x", "source": "s", "event_type": "e", "timestamp": NOW - 5}))

    record = next(sink.read_all())
    assert record.timestamp == NOW - 5
    assert INGEST_FLAG not in record.attributes


async def test_numeric_pair_attributes_are_stored_as_a_dict_and_flagged(tmp_path: Path):
    sink = JsonlFileSink(tmp_path / "audit.jsonl", clock=lambda: NOW)
    await sink.write(AuditRecord.from_dict({"id": "x", "timestamp": NOW, "attributes": [[10, "a"], [9, "b"]]}))

    record = next(sink.read_all())
    assert record.attributes == {"10": "a", "9": "b", INGEST_FLAG: True}
    sink.verify()


def test_the_read_back_check_refuses_a_line_that_would_not_verify():
    record = AuditRecord(id="x", timestamp=NOW, source="s", event_type="e").with_hash_chain(prev_hash=GENESIS_PREV_HASH)
    tampered = json.dumps({**record.to_dict(), "hash": "f" * 64}, separators=(",", ":")).encode()

    with pytest.raises(UnstorableRecordError):
        _check_reads_back(tampered, GENESIS_PREV_HASH, "x")


def test_the_read_back_check_accepts_a_line_that_verifies():
    record = AuditRecord(id="x", timestamp=NOW, source="s", event_type="e").with_hash_chain(prev_hash=GENESIS_PREV_HASH)

    _check_reads_back(record.to_json().encode(), GENESIS_PREV_HASH, "x")


def test_the_read_back_check_refuses_a_line_linked_to_the_wrong_predecessor():
    record = AuditRecord(id="x", timestamp=NOW, source="s", event_type="e").with_hash_chain(prev_hash="a" * 64)

    with pytest.raises(UnstorableRecordError):
        _check_reads_back(record.to_json().encode(), GENESIS_PREV_HASH, "x")


async def test_an_append_whose_line_would_not_read_back_raises_and_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / "audit.jsonl"
    sink = JsonlFileSink(target)
    await append(sink, "seed")
    before = target.read_bytes()

    def _refuse(*_args, **_kwargs):
        raise ValueError("does not verify")

    monkeypatch.setattr(sink_module, "validate_chained", _refuse)
    with pytest.raises(UnstorableRecordError):
        await append(sink, "bad")
    assert target.read_bytes() == before

    monkeypatch.undo()
    await append(sink, "good")
    assert [r.id for r in sink.read_all()] == ["seed", "good"]
    sink.verify()


async def test_two_sinks_on_one_ledger_keep_the_chain_valid(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    first_writer = JsonlFileSink(target)
    second_writer = JsonlFileSink(target)
    await append(first_writer, "a")
    await append(second_writer, "b")
    await append(first_writer, "c")

    assert [r.id for r in JsonlFileSink(target).read_all()] == ["a", "b", "c"]
    JsonlFileSink(target).verify()


async def test_a_ledger_replaced_by_another_file_of_the_same_size_is_not_trusted_from_the_cache(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    other = tmp_path / "other.jsonl"
    sink = JsonlFileSink(target, clock=lambda: NOW)
    await sink.write(AuditRecord(id="aa", timestamp=NOW, source="s", event_type="e"))
    await JsonlFileSink(other, clock=lambda: NOW).write(AuditRecord(id="bb", timestamp=NOW, source="s", event_type="e"))
    assert other.stat().st_size == target.stat().st_size
    assert other.read_bytes() != target.read_bytes()
    stale_head = sink._cached_last_hash
    os.replace(other, target)

    await sink.write(AuditRecord(id="cc", timestamp=NOW, source="s", event_type="e"))

    assert sink._cached_last_hash != stale_head
    assert [r.id for r in sink.read_all()] == ["bb", "cc"]
    sink.verify()


async def test_a_ledger_that_grew_under_the_same_inode_is_not_trusted_from_the_cache(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    sink = JsonlFileSink(target)
    await append(sink, "a")
    inode = target.stat().st_ino
    await append(JsonlFileSink(target), "b")
    assert target.stat().st_ino == inode

    await append(sink, "c")

    assert [r.id for r in sink.read_all()] == ["a", "b", "c"]
    sink.verify()
