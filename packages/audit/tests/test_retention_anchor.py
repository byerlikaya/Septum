"""Retention anchor: the chain checkpoint a prune writes, its field and hash validation, and tamper and crash behaviour."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from septum_audit import AuditRecord, JsonlFileSink, RetentionPolicy
from septum_audit.anchor import RetentionAnchor, anchor_path, read_anchor
from septum_audit.events import GENESIS_PREV_HASH, LedgerIntegrityError

from _helpers import NOW, ids, put, seed

OTHER_HASH = "a" * 64


def _anchor_kwargs(**overrides):
    base = dict(hash=GENESIS_PREV_HASH, previous_hash=GENESIS_PREV_HASH, removed_total=0, pruned_at=NOW)
    base.update(overrides)
    return base


async def test_age_prune_writes_anchor_at_first_survivor_link(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    records = await seed(target, [10, 5, 0.5, 0.1])

    removed = await JsonlFileSink(target).apply_retention(RetentionPolicy(max_age_days=2), now=NOW)

    assert removed == 2
    anchor = read_anchor(target)
    assert anchor is not None
    assert anchor.hash == records[1].hash
    assert list(JsonlFileSink(target).read_all())[0].prev_hash == anchor.hash
    assert anchor.previous_hash == GENESIS_PREV_HASH
    assert anchor.removed_total == 2
    assert anchor.pruned_at == NOW
    JsonlFileSink(target).verify()


async def test_pruned_ledger_fails_without_anchor(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    await seed(target, [10, 0.5])
    await JsonlFileSink(target).apply_retention(RetentionPolicy(max_age_days=2), now=NOW)

    anchor_path(target).unlink()

    with pytest.raises(ValueError, match="prev_hash mismatch"):
        JsonlFileSink(target).verify()


async def test_anchor_edited_to_other_hash_fails_verification(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    await seed(target, [10, 0.5])
    await JsonlFileSink(target).apply_retention(RetentionPolicy(max_age_days=2), now=NOW)
    data = json.loads(anchor_path(target).read_text())
    data["hash"] = "f" * 64
    data["previous_hash"] = "e" * 64
    anchor_path(target).write_text(json.dumps(data))

    with pytest.raises(ValueError, match="prev_hash mismatch"):
        JsonlFileSink(target).verify()


async def test_malformed_anchor_fails_loudly_without_echoing_content(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    await seed(target, [10, 0.5])
    await JsonlFileSink(target).apply_retention(RetentionPolicy(max_age_days=2), now=NOW)
    anchor_path(target).write_text('{"hash": "not-a-secret-but-bad"')

    with pytest.raises(ValueError) as excinfo:
        JsonlFileSink(target).verify()
    assert "retention anchor is malformed" in str(excinfo.value)
    assert "not-a-secret" not in str(excinfo.value)


async def test_survivor_edit_fails_verification(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    await seed(target, [10, 0.5, 0.2])
    await JsonlFileSink(target).apply_retention(RetentionPolicy(max_age_days=2), now=NOW)
    text_lines = target.read_text().strip().split("\n")
    payload = json.loads(text_lines[-1])
    payload["attributes"] = {"index": 999}
    text_lines[-1] = json.dumps(payload, separators=(",", ":"))
    target.write_text("\n".join(text_lines) + "\n")

    with pytest.raises(ValueError, match="hash mismatch"):
        JsonlFileSink(target).verify()


async def test_second_prune_chains_anchor_and_accumulates_count(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    records = await seed(target, [10, 5, 1, 0.5, 0.1])
    sink = JsonlFileSink(target)

    await sink.apply_retention(RetentionPolicy(max_age_days=7), now=NOW)
    first = read_anchor(target)
    await sink.apply_retention(RetentionPolicy(max_records=2), now=NOW)
    second = read_anchor(target)

    assert second is not None and first is not None
    assert second.previous_hash == first.hash
    assert second.removed_total == 3
    assert second.hash == records[2].hash
    assert ids(target) == ["r-3", "r-4"]
    sink.verify()


async def test_prune_that_removes_nothing_writes_no_anchor(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    await seed(target, [0.5, 0.1])

    assert await JsonlFileSink(target).apply_retention(RetentionPolicy(max_age_days=2), now=NOW) == 0
    assert not anchor_path(target).exists()


async def test_full_purge_anchors_last_hash_and_next_append_chains_from_it(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    records = await seed(target, [10, 9])
    sink = JsonlFileSink(target)

    assert await sink.apply_retention(RetentionPolicy(max_age_days=1), now=NOW) == 2
    anchor = read_anchor(target)
    assert anchor is not None
    assert anchor.hash == records[-1].hash
    assert target.read_text() == ""

    await put(sink, AuditRecord(id="fresh", timestamp=NOW, source="s", event_type="e"))
    fresh = list(sink.read_all())
    assert fresh[0].prev_hash == anchor.hash
    sink.verify()


async def test_crash_after_anchor_commit_before_ledger_replace_still_verifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / "audit.jsonl"
    await seed(target, [10, 5, 0.5])
    sink = JsonlFileSink(target)
    original_replace = os.replace
    before = target.read_text()

    def _crash_on_ledger(src, dst, *args, **kwargs):
        if str(dst) == str(target):
            raise OSError("simulated crash before ledger replace")
        return original_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", _crash_on_ledger)
    with pytest.raises(OSError):
        await sink.apply_retention(RetentionPolicy(max_age_days=2), now=NOW)
    monkeypatch.undo()

    assert target.read_text() == before
    assert read_anchor(target) is not None
    sink.verify()

    assert await sink.apply_retention(RetentionPolicy(max_age_days=2), now=NOW) == 2
    assert ids(target) == ["r-2"]
    sink.verify()


async def test_crash_while_writing_anchor_leaves_ledger_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / "audit.jsonl"
    await seed(target, [10, 0.5])
    sink = JsonlFileSink(target)
    original_replace = os.replace
    before = target.read_text()

    def _crash_on_anchor(src, dst, *args, **kwargs):
        if str(dst) == str(anchor_path(target)):
            raise OSError("simulated crash before anchor commit")
        return original_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", _crash_on_anchor)
    with pytest.raises(OSError):
        await sink.apply_retention(RetentionPolicy(max_age_days=2), now=NOW)
    monkeypatch.undo()

    assert target.read_text() == before
    assert read_anchor(target) is None
    assert not Path(f"{anchor_path(target)}.tmp").exists()
    sink.verify()


async def test_dropping_the_first_survivor_fails_verification(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    await seed(target, [10, 0.5, 0.4])
    await JsonlFileSink(target).apply_retention(RetentionPolicy(max_age_days=2), now=NOW)
    text_lines = target.read_text().strip().split("\n")
    target.write_text("\n".join(text_lines[1:]) + "\n")

    with pytest.raises(ValueError, match="prev_hash mismatch"):
        JsonlFileSink(target).verify()


async def test_deleting_a_middle_record_fails_verification_after_prune(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    await seed(target, [10, 0.5, 0.4, 0.3])
    await JsonlFileSink(target).apply_retention(RetentionPolicy(max_age_days=2), now=NOW)
    text_lines = target.read_text().strip().split("\n")
    del text_lines[1]
    target.write_text("\n".join(text_lines) + "\n")

    with pytest.raises(ValueError, match="prev_hash mismatch"):
        JsonlFileSink(target).verify()


def test_legacy_prefix_needs_both_links_to_be_genesis():
    with pytest.raises(ValueError):
        RetentionAnchor(**_anchor_kwargs(previous_hash=OTHER_HASH, legacy_prefix=True))
    with pytest.raises(ValueError):
        RetentionAnchor(**_anchor_kwargs(hash=OTHER_HASH, legacy_prefix=True))
    assert RetentionAnchor(**_anchor_kwargs(legacy_prefix=True)).legacy_prefix is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("legacy_prefix", 1),
        ("legacy_prefix", "false"),
        ("legacy_prefix", "true"),
        ("legacy_prefix", 0),
        ("legacy_prefix", None),
        ("removed_total", -1),
        ("removed_total", 0.5),
        ("removed_total", True),
        ("removed_total", "3"),
        ("pruned_at", "nan"),
        ("pruned_at", float("nan")),
        ("pruned_at", True),
    ],
)
def test_anchor_with_a_mistyped_field_is_unreadable(tmp_path: Path, field: str, value):
    target = tmp_path / "audit.jsonl"
    payload = {"hash": OTHER_HASH, "previous_hash": GENESIS_PREV_HASH, "removed_total": 1,
               "pruned_at": NOW, "legacy_prefix": False}
    payload[field] = value
    anchor_path(target).write_text(json.dumps(payload, allow_nan=True), encoding="utf-8")

    with pytest.raises(LedgerIntegrityError, match="retention anchor is malformed"):
        read_anchor(target)


@pytest.mark.parametrize("body", ["{}", "[]", "null", "123", '"text"', "{not json"])
def test_read_anchor_reports_every_malformed_shape_as_integrity_error(tmp_path: Path, body: str):
    target = tmp_path / "audit.jsonl"
    anchor_path(target).write_text(body, encoding="utf-8")

    with pytest.raises(LedgerIntegrityError, match="malformed"):
        read_anchor(target)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("hash", "not-hex"),
        ("hash", OTHER_HASH.upper()),
        ("hash", OTHER_HASH + "\n"),
        ("hash", "a" * 63),
        ("previous_hash", OTHER_HASH.upper()),
        ("previous_hash", OTHER_HASH + "\n"),
    ],
)
def test_anchor_hash_must_be_64_lowercase_hex_without_a_newline(field: str, value: str):
    with pytest.raises(ValueError):
        RetentionAnchor(**_anchor_kwargs(**{field: value}))
