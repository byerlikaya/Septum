"""Age + count cap retention with atomic rewrite semantics."""

from __future__ import annotations

import json
from pathlib import Path

from septum_audit import (
    AuditRecord,
    JsonlFileSink,
    RetentionPolicy,
    apply_retention_to_jsonl,
)


async def _seed(path: Path, records: list[AuditRecord]) -> None:
    sink = JsonlFileSink(path)
    for record in records:
        await sink.write(record)


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

    surviving = [json.loads(l) for l in target.read_text().strip().split("\n")]
    assert [r["id"] for r in surviving] == ["new"]


async def test_count_cap_keeps_most_recent_records(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    await _seed(
        target,
        [AuditRecord(id=f"r-{i}", source="s", event_type="e") for i in range(5)],
    )

    removed = apply_retention_to_jsonl(target, RetentionPolicy(max_records=2))
    assert removed == 3

    surviving = [json.loads(l) for l in target.read_text().strip().split("\n")]
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

    surviving = [json.loads(l) for l in target.read_text().strip().split("\n")]
    assert [r["id"] for r in surviving] == ["recent-2", "recent-3"]


def test_corrupt_lines_count_as_removals(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    target.write_text(
        '{"id":"good","timestamp":1700000000,"source":"s","event_type":"e","attributes":{}}\n'
        "broken-json-line\n",
        encoding="utf-8",
    )

    removed = apply_retention_to_jsonl(
        target, RetentionPolicy(max_age_days=365), now=1_700_000_100.0
    )
    assert removed == 1
    surviving = [json.loads(l) for l in target.read_text().strip().split("\n")]
    assert [r["id"] for r in surviving] == ["good"]
