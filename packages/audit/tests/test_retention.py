"""Age + count cap retention with atomic rewrite semantics."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest

from septum_audit import (
    AuditRecord,
    JsonlFileSink,
    RetentionPolicy,
    apply_retention_to_jsonl,
)
from septum_audit.anchor import read_anchor
from septum_audit.events import GENESIS_PREV_HASH, LedgerIntegrityError

DAY = 86400.0
NOW = 1_700_000_000.0


async def _seed(path: Path, records: list[AuditRecord]) -> None:
    sink = JsonlFileSink(path)
    for record in records:
        await _put(sink, record)


def test_noop_policy_returns_zero_and_leaves_file_untouched(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    target.write_text('{"id":"x","timestamp":1,"source":"s","event_type":"e","attributes":{}}\n')
    before = target.read_text()

    removed = apply_retention_to_jsonl(target, RetentionPolicy())
    assert removed == 0
    assert target.read_text() == before


def test_apply_retention_to_missing_file_returns_zero(tmp_path: Path):
    removed = apply_retention_to_jsonl(
        tmp_path / "never.jsonl", RetentionPolicy(max_age_days=1)
    )
    assert removed == 0


async def test_age_cap_drops_records_older_than_cutoff(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    now = 1_700_000_000.0
    one_day = 86400.0
    await _seed(
        target,
        [
            AuditRecord(id="old", timestamp=now - 5 * one_day, source="s", event_type="e"),
            AuditRecord(id="new", timestamp=now - 0.5 * one_day, source="s", event_type="e"),
        ],
    )

    removed = apply_retention_to_jsonl(
        target, RetentionPolicy(max_age_days=2), now=now
    )
    assert removed == 1

    surviving = [json.loads(line) for line in target.read_text().strip().split("\n")]
    assert [r["id"] for r in surviving] == ["new"]


async def test_count_cap_keeps_most_recent_records(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    await _seed(
        target,
        [AuditRecord(id=f"r-{i}", source="s", event_type="e") for i in range(5)],
    )

    removed = apply_retention_to_jsonl(target, RetentionPolicy(max_records=2))
    assert removed == 3

    surviving = [json.loads(line) for line in target.read_text().strip().split("\n")]
    assert [r["id"] for r in surviving] == ["r-3", "r-4"]


async def test_combined_age_and_count_caps_apply_in_sequence(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    now = 1_700_000_000.0
    one_day = 86400.0
    await _seed(
        target,
        [
            AuditRecord(id="too-old", timestamp=now - 10 * one_day, source="s", event_type="e"),
            AuditRecord(id="recent-1", timestamp=now - 1 * one_day, source="s", event_type="e"),
            AuditRecord(id="recent-2", timestamp=now - 0.5 * one_day, source="s", event_type="e"),
            AuditRecord(id="recent-3", timestamp=now - 0.1 * one_day, source="s", event_type="e"),
        ],
    )

    removed = apply_retention_to_jsonl(
        target,
        RetentionPolicy(max_age_days=2, max_records=2),
        now=now,
    )
    # 1 dropped by age + 1 dropped by count cap = 2 total.
    assert removed == 2

    surviving = [json.loads(line) for line in target.read_text().strip().split("\n")]
    assert [r["id"] for r in surviving] == ["recent-2", "recent-3"]


def _expiring_then_good(path: Path, good_text: str) -> str:
    """Write one expired, chained record followed by ``good_text``; return the expired record's hash."""
    old = AuditRecord(id="old", timestamp=1.0, source="s", event_type="e").with_hash_chain(
        prev_hash=GENESIS_PREV_HASH
    )
    path.write_text(old.to_json() + "\n" + good_text, encoding="utf-8")
    return old.hash


def test_malformed_terminated_line_halts_retention_and_prunes_nothing(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    good = AuditRecord(id="good", timestamp=1_700_000_000.0, source="s", event_type="e")
    _expiring_then_good(target, "")
    old_line = target.read_text(encoding="utf-8")
    before = old_line + good.with_hash_chain(prev_hash=json.loads(old_line)["hash"]).to_json() + "\n" + "broken-json-line\n"
    target.write_text(before, encoding="utf-8")

    with pytest.raises(LedgerIntegrityError, match="line 3 is malformed"):
        apply_retention_to_jsonl(target, RetentionPolicy(max_age_days=365), now=1_700_000_100.0)

    assert target.read_text(encoding="utf-8") == before
    assert not Path(f"{target}.anchor").exists()


def test_torn_final_line_is_dropped_as_crash_residue(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    good = AuditRecord(id="good", timestamp=1_700_000_000.0, source="s", event_type="e")
    prev = _expiring_then_good(target, "")
    tail = good.with_hash_chain(prev_hash=prev).to_json()
    target.write_text(target.read_text(encoding="utf-8") + tail + "\n" + '{"id":"tor', encoding="utf-8")

    removed = apply_retention_to_jsonl(
        target, RetentionPolicy(max_age_days=365), now=1_700_000_100.0
    )
    assert removed == 2
    surviving = [json.loads(line) for line in target.read_text().strip().split("\n")]
    assert [r["id"] for r in surviving] == ["good"]


def sink_anchor_hash(tmp_path: Path) -> str:
    anchor = read_anchor(tmp_path / "audit.jsonl")
    assert anchor is not None
    return anchor.hash


def _assert_survivor_chain_links(records: list[AuditRecord]) -> None:
    for previous, current in zip(records, records[1:]):
        assert current.prev_hash == previous.hash


def test_policy_rejects_values_below_one():
    with pytest.raises(ValueError):
        RetentionPolicy(max_records=0)
    with pytest.raises(ValueError):
        RetentionPolicy(max_age_days=-1)


async def test_sink_apply_retention_noop_leaves_file_untouched(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    sink = JsonlFileSink(target)
    await _put(sink, AuditRecord(id="a", source="s", event_type="e"))
    before = target.read_text()

    assert await sink.apply_retention(RetentionPolicy()) == 0
    assert target.read_text() == before


@pytest.mark.parametrize(
    ("policy", "expected_ids"),
    [
        (RetentionPolicy(max_age_days=2), ["fresh-1", "fresh-2", "fresh-3"]),
        (RetentionPolicy(max_records=2), ["fresh-2", "fresh-3"]),
        (RetentionPolicy(max_age_days=2, max_records=1), ["fresh-3"]),
    ],
    ids=["age-cap", "count-cap", "both-caps"],
)
async def test_sink_apply_retention_applies_each_cap(tmp_path: Path, policy, expected_ids):
    target = tmp_path / "audit.jsonl"
    sink = JsonlFileSink(target)
    await _put(sink, AuditRecord(id="old", timestamp=NOW - 10 * DAY, source="s", event_type="e"))
    for index in (1, 2, 3):
        await _put(sink,
            AuditRecord(id=f"fresh-{index}", timestamp=NOW - 0.1 * DAY, source="s", event_type="e")
        )

    await sink.apply_retention(policy, now=NOW)

    assert [r.id for r in sink.read_all()] == expected_ids
    _assert_survivor_chain_links(list(sink.read_all()))


async def test_sink_apply_retention_keeps_file_mode_0o640(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    sink = JsonlFileSink(target)
    await _put(sink, AuditRecord(id="old", timestamp=NOW - 10 * DAY, source="s", event_type="e"))
    await _put(sink, AuditRecord(id="new", timestamp=NOW, source="s", event_type="e"))

    await sink.apply_retention(RetentionPolicy(max_age_days=2), now=NOW)

    assert oct(target.stat().st_mode & 0o777) == "0o640"


async def test_append_after_full_purge_chains_from_anchor(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    sink = JsonlFileSink(target)
    await _put(sink, AuditRecord(id="old", timestamp=NOW - 10 * DAY, source="s", event_type="e"))

    assert await sink.apply_retention(RetentionPolicy(max_age_days=1), now=NOW) == 1
    await _put(sink, AuditRecord(id="fresh", timestamp=NOW, source="s", event_type="e"))

    records = list(sink.read_all())
    assert [r.id for r in records] == ["fresh"]
    assert records[0].prev_hash == sink_anchor_hash(tmp_path)
    sink.verify()


async def test_sink_retention_serialises_with_concurrent_writes(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    sink = JsonlFileSink(target)
    for index in range(50):
        await _put(sink,
            AuditRecord(id=f"old-{index}", timestamp=NOW - 10 * DAY, source="s", event_type="e")
        )
    fresh = [
        AuditRecord(id=f"new-{index}", timestamp=NOW, source="s", event_type="e")
        for index in range(30)
    ]

    async def _writes() -> None:
        for record in fresh:
            await _put(sink, record)

    removed, _ = await asyncio.gather(
        sink.apply_retention(RetentionPolicy(max_age_days=2), now=NOW),
        _writes(),
    )

    assert removed == 50
    survivors = list(sink.read_all())
    assert [r.id for r in survivors] == [r.id for r in fresh]
    _assert_survivor_chain_links(survivors)


def test_rewrite_in_thread_never_loses_or_duplicates_concurrent_appends(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    seed = [
        AuditRecord(id=f"old-{index}", timestamp=NOW - 10 * DAY, source="s", event_type="e")
        for index in range(200)
    ]

    async def _seed() -> None:
        sink = JsonlFileSink(target)
        for record in seed:
            await _put(sink, record)

    asyncio.run(_seed())

    fresh = [
        AuditRecord(id=f"new-{index}", timestamp=NOW, source="s", event_type="e")
        for index in range(150)
    ]

    async def _append_all() -> None:
        writer = JsonlFileSink(target)
        for record in fresh:
            await _put(writer, record)

    def _retain_repeatedly() -> None:
        for _ in range(25):
            apply_retention_to_jsonl(target, RetentionPolicy(max_age_days=2), now=NOW)

    threads = [
        threading.Thread(target=lambda: asyncio.run(_append_all())),
        threading.Thread(target=_retain_repeatedly),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads)

    survivors = [json.loads(line) for line in target.read_text().strip().split("\n")]
    assert [r["id"] for r in survivors] == [r.id for r in fresh]


async def _put(sink, record):
    """Write through a sink stamped with the record's own timestamp, so age tests control received_at."""
    from septum_audit import JsonlFileSink as _Sink

    await _Sink(sink.path, clock=lambda: record.timestamp).write(record)
