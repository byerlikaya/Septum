"""Retention policy: age follows received_at, count caps keep a slack band, unknown ages follow their successor, and a clean ledger is skipped."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from septum_audit import AuditRecord, JsonlFileSink, RetentionPolicy
from septum_audit import retention as retention_module
from septum_audit.anchor import read_anchor

from _helpers import DAY, NOW, ids, ledger_with_tail, legacy_line, put, write_at


UNKNOWN_AGE_LINES = {
    "zero": legacy_line("unknown", "0"),
    "nan": legacy_line("unknown", "NaN"),
    "missing": '{"id":"unknown","source":"s","event_type":"e","attributes":{}}',
}


def _ledger(path: Path, *texts: str) -> None:
    path.write_text("\n".join(texts) + "\n", encoding="utf-8")


async def test_backdated_record_in_the_middle_is_kept_and_the_chain_verifies(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    sink = JsonlFileSink(target)
    for index, age in enumerate([10, 0.5, 10, 0.1]):
        await put(sink, AuditRecord(id=f"r-{index}", timestamp=NOW - age * DAY, source="s", event_type="e"))

    assert await sink.apply_retention(RetentionPolicy(max_age_days=2), now=NOW) == 1

    assert ids(target) == ["r-1", "r-2", "r-3"]
    sink.verify()


async def test_old_timestamp_with_fresh_received_at_is_not_pruned(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    await write_at(target, AuditRecord(id="backdated", timestamp=1.0, source="s", event_type="e"), NOW)

    assert await JsonlFileSink(target).apply_retention(RetentionPolicy(max_age_days=1), now=NOW) == 0
    assert ids(target) == ["backdated"]


async def test_fresh_timestamp_with_old_received_at_is_pruned(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    await write_at(target, AuditRecord(id="old", timestamp=NOW, source="s", event_type="e"), NOW - 10 * DAY)
    await write_at(target, AuditRecord(id="new", timestamp=NOW, source="s", event_type="e"), NOW)

    assert await JsonlFileSink(target).apply_retention(RetentionPolicy(max_age_days=1), now=NOW) == 1
    assert ids(target) == ["new"]


@pytest.mark.parametrize(("offset", "removed"), [(-0.5, 1), (0.0, 0), (1.0, 0)], ids=["before", "exact", "after"])
async def test_record_is_removed_only_when_strictly_older_than_the_cutoff(tmp_path: Path, offset: float, removed: int):
    target = tmp_path / "audit.jsonl"
    policy = RetentionPolicy(max_age_days=1)
    cutoff = policy.cutoff_timestamp(now=NOW)
    assert cutoff == NOW - DAY
    await write_at(target, AuditRecord(id="edge", timestamp=NOW, source="s", event_type="e"), cutoff + offset)
    await write_at(target, AuditRecord(id="fresh", timestamp=NOW, source="s", event_type="e"), NOW)

    assert await JsonlFileSink(target).apply_retention(policy, now=NOW) == removed
    assert ids(target) == (["fresh"] if removed else ["edge", "fresh"])


@pytest.mark.parametrize(("cap", "expected"), [(1, 2), (5, 6), (20, 21), (100, 105)])
def test_count_threshold_has_at_least_one_slot_of_slack(cap: int, expected: int):
    assert RetentionPolicy(max_records=cap).count_threshold() == expected


@pytest.mark.parametrize("cap", [20, 100])
async def test_count_cap_prunes_only_past_the_slack_then_trims_to_the_exact_cap(tmp_path: Path, cap: int):
    target = tmp_path / "audit.jsonl"
    policy = RetentionPolicy(max_records=cap)
    threshold = policy.count_threshold()
    sink = JsonlFileSink(target)
    for index in range(threshold):
        await sink.write(AuditRecord(id=f"r-{index}", source="s", event_type="e"))

    assert await sink.apply_retention(policy) == 0
    assert len(ids(target)) == threshold

    await sink.write(AuditRecord(id="last", source="s", event_type="e"))
    assert await sink.apply_retention(policy) == threshold + 1 - cap

    kept = ids(target)
    assert len(kept) == cap
    assert kept[-1] == "last"
    sink.verify()


async def test_forced_pass_does_not_trim_a_ledger_that_is_within_the_count_slack(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    sink = JsonlFileSink(target)
    for index in range(103):
        await sink.write(AuditRecord(id=f"r-{index}", source="s", event_type="e"))
    with target.open("a", encoding="utf-8") as fh:
        fh.write('{"id":"tor')

    assert await sink.apply_retention(RetentionPolicy(max_records=100)) == 1
    assert len(ids(target)) == 103
    sink.verify()


async def test_unterminated_tail_forces_a_pass_even_when_nothing_else_expires(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    await write_at(target, AuditRecord(id="fresh", timestamp=NOW, source="s", event_type="e"), NOW)
    with target.open("a", encoding="utf-8") as fh:
        fh.write('{"id":"tor')

    assert await JsonlFileSink(target).apply_retention(RetentionPolicy(max_age_days=365), now=NOW) == 1
    assert ids(target) == ["fresh"]


async def test_torn_tail_under_the_count_cap_is_still_cleaned_up(tmp_path: Path):
    target, sink = await ledger_with_tail(tmp_path, '{"id":"tor')

    assert await sink.apply_retention(RetentionPolicy(max_records=5)) == 1

    assert target.read_text(encoding="utf-8").endswith("\n")
    assert ids(target) == ["first"]


@pytest.mark.parametrize("policy", [RetentionPolicy(max_age_days=365), RetentionPolicy(max_records=3)], ids=["age", "count"])
async def test_pass_is_skipped_when_nothing_can_expire_so_the_chain_is_not_read(tmp_path: Path, policy: RetentionPolicy):
    target = tmp_path / "audit.jsonl"
    sink = JsonlFileSink(target)
    for index in range(3):
        await put(sink, AuditRecord(id=f"r-{index}", timestamp=NOW, source="s", event_type="e"))
    text_lines = target.read_text(encoding="utf-8").split("\n")
    text_lines[1] = text_lines[1].replace('"event_type":"e"', '"event_type":"x"')
    target.write_text("\n".join(text_lines), encoding="utf-8")
    before = target.read_bytes()

    assert await sink.apply_retention(policy, now=NOW) == 0
    assert target.read_bytes() == before
    assert read_anchor(target) is None


@pytest.mark.parametrize("form", ["zero", "nan", "missing"])
async def test_unknown_age_line_is_removed_with_its_expired_successor(tmp_path: Path, form: str):
    target = tmp_path / "audit.jsonl"
    _ledger(target, UNKNOWN_AGE_LINES[form], legacy_line("expired", "1.0"))

    assert retention_module.apply_retention_to_jsonl(target, RetentionPolicy(max_age_days=1), now=NOW) == 2
    assert target.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("form", ["zero", "nan", "missing"])
@pytest.mark.parametrize("unknowns", [1, 2])
def test_run_of_unknown_age_lines_after_an_expired_record_is_kept_with_its_fresh_successor(
    tmp_path: Path, form: str, unknowns: int
):
    target = tmp_path / "audit.jsonl"
    kept = [UNKNOWN_AGE_LINES[form]] * unknowns
    _ledger(target, legacy_line("expired", "1.0"), *kept, legacy_line("fresh", str(NOW)))

    assert retention_module.apply_retention_to_jsonl(target, RetentionPolicy(max_age_days=1), now=NOW) == 1
    assert [json.loads(line)["id"] for line in target.read_text().split("\n") if line] == (
        ["unknown"] * unknowns + ["fresh"]
    )


@pytest.mark.parametrize("form", ["zero", "nan", "missing"])
def test_unknown_age_line_with_a_fresh_successor_is_kept_without_a_removal(tmp_path: Path, form: str):
    target = tmp_path / "audit.jsonl"
    _ledger(target, UNKNOWN_AGE_LINES[form], legacy_line("fresh", str(NOW)))
    before = target.read_text(encoding="utf-8")

    assert retention_module.apply_retention_to_jsonl(target, RetentionPolicy(max_age_days=1), now=NOW) == 0
    assert target.read_text(encoding="utf-8") == before


def test_unknown_age_line_last_in_the_ledger_is_kept(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    _ledger(target, legacy_line("expired", "1.0"), UNKNOWN_AGE_LINES["zero"])

    assert retention_module.apply_retention_to_jsonl(target, RetentionPolicy(max_age_days=1), now=NOW) == 1
    assert ids(target) == ["unknown"]


def test_a_run_of_unknown_age_lines_takes_the_age_of_the_record_after_the_run(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    _ledger(
        target,
        UNKNOWN_AGE_LINES["zero"],
        UNKNOWN_AGE_LINES["nan"],
        legacy_line("expired", "1.0"),
        legacy_line("fresh", str(NOW)),
    )

    assert retention_module.apply_retention_to_jsonl(target, RetentionPolicy(max_age_days=1), now=NOW) == 3
    assert ids(target) == ["fresh"]


@pytest.mark.parametrize("value", [1.5, "3", 0, -1])
def test_policy_rejects_non_whole_or_non_positive_caps(value):
    with pytest.raises(ValueError):
        RetentionPolicy(max_age_days=value)
    with pytest.raises(ValueError):
        RetentionPolicy(max_records=value)
