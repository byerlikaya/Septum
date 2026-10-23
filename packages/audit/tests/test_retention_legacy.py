"""Legacy ledger lines: hash-less prefixes, forged prefixes, and non-finite values written before the hash covered them."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from septum_audit import AuditRecord, JsonlFileSink, RetentionPolicy
from septum_audit import retention as retention_module
from septum_audit.anchor import RetentionAnchor, anchor_path, read_anchor, write_anchor
from septum_audit.events import GENESIS_PREV_HASH, LedgerIntegrityError

from _helpers import NOW, append, chain, chained, ids, strip_chain


async def test_sink_appends_to_a_legacy_ledger_by_chaining_from_genesis(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    target.write_text(
        json.dumps({"id": "legacy", "timestamp": NOW, "source": "s", "event_type": "e", "attributes": {}}) + "\n",
        encoding="utf-8",
    )
    sink = JsonlFileSink(target)

    await append(sink, "new")

    records = list(sink.read_all())
    assert records[-1].prev_hash == GENESIS_PREV_HASH
    sink.verify()


async def test_hash_less_line_after_a_chained_line_halts_retention_and_verify(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    chained_record = chained("old", GENESIS_PREV_HASH, timestamp=1.0)
    target.write_text(
        chained_record.to_json() + "\n" + json.dumps({"id": "sneaky", "timestamp": 1.0, "source": "s", "event_type": "e"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(LedgerIntegrityError, match="lacks prev_hash"):
        retention_module.apply_retention_to_jsonl(target, RetentionPolicy(max_age_days=1), now=NOW)
    with pytest.raises(LedgerIntegrityError, match="lacks prev_hash"):
        JsonlFileSink(target).verify()


async def test_hash_less_line_is_refused_once_an_anchor_exists(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    first = chained("old", GENESIS_PREV_HASH, timestamp=1.0)
    second = chained("kept", first.hash, timestamp=NOW)
    target.write_text(first.to_json() + "\n" + second.to_json() + "\n", encoding="utf-8")
    assert retention_module.apply_retention_to_jsonl(target, RetentionPolicy(max_age_days=1), now=NOW) == 1
    assert read_anchor(target).legacy_prefix is False

    target.write_text(
        json.dumps({"id": "sneaky", "timestamp": NOW, "source": "s", "event_type": "e"}) + "\n" + target.read_text(),
        encoding="utf-8",
    )
    with pytest.raises(LedgerIntegrityError, match="lacks prev_hash"):
        JsonlFileSink(target).verify()


async def test_legacy_prefix_is_pruned_before_chained_records_and_the_chain_still_verifies(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    legacy = json.dumps({"id": "legacy", "timestamp": 1.0, "source": "s", "event_type": "e", "attributes": {}})
    kept = chained("kept", GENESIS_PREV_HASH, timestamp=NOW)
    target.write_text(legacy + "\n" + kept.to_json() + "\n", encoding="utf-8")

    assert retention_module.apply_retention_to_jsonl(target, RetentionPolicy(max_age_days=1), now=NOW) == 1

    assert ids(target) == ["kept"]
    assert read_anchor(target).legacy_prefix is False
    JsonlFileSink(target).verify()


async def test_hash_less_prefix_is_accepted_and_pruned_by_policy(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    legacy_old = json.dumps({"id": "legacy-old", "timestamp": NOW - 10 * 86400, "source": "s", "event_type": "e"})
    legacy_new = json.dumps({"id": "legacy-new", "timestamp": NOW, "source": "s", "event_type": "e"})
    target.write_text(legacy_old + "\n" + legacy_new + "\n", encoding="utf-8")

    assert await JsonlFileSink(target).apply_retention(RetentionPolicy(max_age_days=2), now=NOW) == 1

    assert ids(target) == ["legacy-new"]
    assert read_anchor(target).legacy_prefix is True


def test_non_finite_legacy_timestamp_is_kept_with_no_successor_and_does_not_pin(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    target.write_text('{"id":"legacy","timestamp":NaN,"source":"s","event_type":"e","attributes":{}}\n', encoding="utf-8")
    before = target.read_text(encoding="utf-8")

    assert retention_module.apply_retention_to_jsonl(target, RetentionPolicy(max_age_days=1), now=NOW) == 0
    assert target.read_text(encoding="utf-8") == before
    assert retention_module.pin_ahead_seconds(target, NOW) is None


def test_forged_legacy_prefix_with_a_non_genesis_anchor_halts(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    records = chain(5)
    text = [strip_chain(r) for r in records[:3]] + [r.to_json() for r in records[3:]]
    target.write_text("\n".join(text) + "\n", encoding="utf-8")
    anchor_path(target).write_text(json.dumps({
        "hash": records[3].prev_hash,
        "previous_hash": GENESIS_PREV_HASH,
        "removed_total": 0,
        "pruned_at": NOW,
        "legacy_prefix": True,
    }), encoding="utf-8")

    with pytest.raises(LedgerIntegrityError):
        read_anchor(target)
    with pytest.raises(LedgerIntegrityError):
        JsonlFileSink(target).verify()


def test_forged_genesis_legacy_prefix_halts_on_the_first_chained_record(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    records = chain(5)
    text = [strip_chain(r) for r in records[:3]] + [r.to_json() for r in records[3:]]
    target.write_text("\n".join(text) + "\n", encoding="utf-8")
    write_anchor(target, RetentionAnchor(
        hash=GENESIS_PREV_HASH, previous_hash=GENESIS_PREV_HASH, removed_total=0, pruned_at=NOW, legacy_prefix=True,
    ))

    with pytest.raises(LedgerIntegrityError, match="prev_hash mismatch"):
        JsonlFileSink(target).verify()


def test_legacy_nan_literal_line_written_by_an_older_serialiser_still_verifies(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    old = AuditRecord(id="old", timestamp=NOW, source="s", event_type="e", attributes={"score": float("nan")})
    old = old.with_hash_chain(prev_hash=GENESIS_PREV_HASH)
    line = json.dumps(old.to_dict(), separators=(",", ":"))
    assert "NaN" in line
    target.write_text(line + "\n", encoding="utf-8")

    JsonlFileSink(target).verify()


def test_authentic_pre_change_nan_line_verifies_and_a_tampered_one_is_a_hash_mismatch(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    good = chained("old", GENESIS_PREV_HASH, timestamp=1.0)
    tampered = chained("nan", good.hash, timestamp=1.0).to_json().replace('"timestamp":1.0', '"timestamp":NaN')
    target.write_text(good.to_json() + "\n" + tampered + "\n", encoding="utf-8")

    with pytest.raises(LedgerIntegrityError, match="hash mismatch"):
        JsonlFileSink(target).verify()


def test_authentic_pre_change_nan_timestamp_line_survives_a_prune_without_pinning(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    old = chained("old", GENESIS_PREV_HASH, timestamp=1.0)
    nan_record = AuditRecord(id="nan", timestamp=float("nan"), source="s", event_type="e")
    nan_record = nan_record.with_hash_chain(prev_hash=old.hash)
    nan_line = json.dumps(nan_record.to_dict(), separators=(",", ":"))
    assert '"timestamp":NaN' in nan_line
    target.write_text(old.to_json() + "\n" + nan_line + "\n", encoding="utf-8")

    assert retention_module.apply_retention_to_jsonl(target, RetentionPolicy(max_age_days=1), now=NOW) == 1

    assert target.read_text(encoding="utf-8") == nan_line + "\n"
    JsonlFileSink(target).verify()
    assert retention_module.pin_ahead_seconds(target, NOW) is None
