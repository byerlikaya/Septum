"""Retention refuses to prune a ledger it cannot verify, rejects malformed lines loudly, and never follows tmp files."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from septum_audit import AuditRecord, JsonlFileSink, RetentionPolicy
from septum_audit import retention as retention_module
from septum_audit.anchor import anchor_path, read_anchor
from septum_audit.events import GENESIS_PREV_HASH, LedgerIntegrityError

from _helpers import NOW, chained, ids, lines, seed


async def test_deleting_in_window_records_and_appending_junk_halts_prune(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    await seed(target, [5, 4, 3, 0.5])
    text_lines = target.read_text().strip().split("\n")
    del text_lines[1:3]
    text_lines.append('{"id":"junk","timestamp":1.0,"prev_hash":"' + "a" * 64 + '","hash":"' + "b" * 64 + '"}')
    target.write_text("\n".join(text_lines) + "\n")
    before = target.read_text()

    with pytest.raises(LedgerIntegrityError, match="chain broken"):
        await JsonlFileSink(target).apply_retention(RetentionPolicy(max_age_days=2), now=NOW)

    assert target.read_text() == before
    assert read_anchor(target) is None
    with pytest.raises(ValueError):
        JsonlFileSink(target).verify()


async def test_deleting_a_record_without_junk_also_halts_prune(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    await seed(target, [5, 4, 0.5])
    text_lines = target.read_text().strip().split("\n")
    del text_lines[1]
    target.write_text("\n".join(text_lines) + "\n")

    with pytest.raises(LedgerIntegrityError, match="chain broken"):
        await JsonlFileSink(target).apply_retention(RetentionPolicy(max_age_days=2), now=NOW)

    assert read_anchor(target) is None


async def test_malformed_middle_line_halts_prune_and_removes_nothing(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    await seed(target, [10, 0.5, 0.4])
    text_lines = target.read_text().strip().split("\n")
    text_lines.insert(1, '{"id": "truncated')
    target.write_text("\n".join(text_lines) + "\n")
    before = target.read_text()

    with pytest.raises(LedgerIntegrityError, match="line 2 is malformed"):
        await JsonlFileSink(target).apply_retention(RetentionPolicy(max_age_days=2), now=NOW)

    assert target.read_text() == before
    assert read_anchor(target) is None


async def test_terminated_malformed_trailing_line_halts_retention(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    await seed(target, [10, 0.5])
    with target.open("a", encoding="utf-8") as fh:
        fh.write("not-json\n")
    before = target.read_text()

    with pytest.raises(LedgerIntegrityError):
        await JsonlFileSink(target).apply_retention(RetentionPolicy(max_age_days=2), now=NOW)

    assert target.read_text() == before
    assert read_anchor(target) is None


async def test_content_edit_with_intact_links_halts_prune(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    await seed(target, [10, 0.5, 0.4])
    text_lines = target.read_text().strip().split("\n")
    edited = json.loads(text_lines[1])
    edited["source"] = "tampered"
    text_lines[1] = json.dumps(edited)
    target.write_text("\n".join(text_lines) + "\n")
    before = target.read_text()

    with pytest.raises(LedgerIntegrityError, match="line 2"):
        await JsonlFileSink(target).apply_retention(RetentionPolicy(max_age_days=2), now=NOW)

    assert target.read_text() == before
    assert read_anchor(target) is None


async def test_duplicate_key_line_halts_retention(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    old = chained("old", GENESIS_PREV_HASH, timestamp=1.0)
    target.write_text(
        old.to_json() + "\n" + '{"id":"a","id":"b","timestamp":1,"source":"s","event_type":"e","prev_hash":"x","hash":"y"}\n',
        encoding="utf-8",
    )

    with pytest.raises(LedgerIntegrityError, match="line 2 is malformed"):
        retention_module.apply_retention_to_jsonl(target, RetentionPolicy(max_age_days=1), now=NOW)


async def test_unknown_field_in_a_ledger_line_halts_retention(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    old = chained("old", GENESIS_PREV_HASH, timestamp=1.0)
    payload = json.loads(old.to_json())
    payload["extra"] = "smuggled"
    target.write_text(old.to_json() + "\n" + json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(LedgerIntegrityError, match="line 2 is malformed"):
        retention_module.apply_retention_to_jsonl(target, RetentionPolicy(max_age_days=1), now=NOW)


async def test_verify_refuses_unknown_fields_instead_of_skipping_the_line(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    record = json.loads(chained("a", GENESIS_PREV_HASH, timestamp=NOW).to_json())
    record["extra"] = "smuggled"
    target.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(LedgerIntegrityError, match="malformed"):
        JsonlFileSink(target).verify()


async def test_non_utf8_terminated_line_is_a_loud_integrity_error(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    old = chained("old", GENESIS_PREV_HASH, timestamp=1.0)
    target.write_bytes(old.to_json().encode() + b"\n\xff\xfe broken\n")

    with pytest.raises(LedgerIntegrityError, match="line 2 is malformed"):
        retention_module.apply_retention_to_jsonl(target, RetentionPolicy(max_age_days=1), now=NOW)


async def test_non_canonical_chained_line_halts_the_scan(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    record = chained("r-0", GENESIS_PREV_HASH, timestamp=NOW, received_at=NOW)
    payload = json.loads(record.to_json())
    target.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(LedgerIntegrityError, match="canonical"):
        JsonlFileSink(target).verify()


async def test_hash_less_line_carrying_received_at_is_refused_by_verify(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    payload = {"id": "legacy", "timestamp": NOW, "received_at": NOW, "source": "s", "event_type": "e", "attributes": {}}
    target.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(LedgerIntegrityError, match="carries received_at"):
        JsonlFileSink(target).verify()


async def test_non_finite_received_at_in_the_ledger_halts_retention(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    record = json.loads(chained("a", GENESIS_PREV_HASH, timestamp=1.0).to_json())
    record["received_at"] = float("nan")
    target.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(LedgerIntegrityError, match="malformed"):
        retention_module.apply_retention_to_jsonl(target, RetentionPolicy(max_age_days=1), now=NOW)


async def test_a_complete_line_with_a_list_received_at_halts_instead_of_failing(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    target.write_text(
        '{"id":"x","timestamp":1.0,"received_at":[1],"source":"s","event_type":"e","attributes":{}}\n',
        encoding="utf-8",
    )

    with pytest.raises(LedgerIntegrityError, match="malformed"):
        JsonlFileSink(target).verify()
    with pytest.raises(LedgerIntegrityError, match="malformed"):
        retention_module.apply_retention_to_jsonl(target, RetentionPolicy(max_age_days=1), now=NOW)


async def test_four_hundred_digit_timestamp_line_halts_as_malformed(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    target.write_text(
        chained("r0", GENESIS_PREV_HASH).to_json().replace('"timestamp":' + str(NOW), '"timestamp":' + "1" * 400) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(LedgerIntegrityError, match="malformed"):
        JsonlFileSink(target).verify()
    with pytest.raises(LedgerIntegrityError, match="malformed"):
        retention_module.apply_retention_to_jsonl(target, RetentionPolicy(max_age_days=1), now=NOW)


async def test_unparseable_first_line_halts_the_full_pass(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    target.write_text("garbage\n", encoding="utf-8")

    with pytest.raises(LedgerIntegrityError, match="line 1 is malformed"):
        retention_module.apply_retention_to_jsonl(target, RetentionPolicy(max_age_days=1), now=NOW)


async def test_non_finite_timestamp_line_whose_hash_does_not_cover_it_halts_retention(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    old = chained("old", GENESIS_PREV_HASH, timestamp=1.0)
    forged = chained("nan", old.hash, timestamp=1.0).to_json().replace('"timestamp":1.0', '"timestamp":NaN')
    assert "NaN" in forged
    target.write_text(old.to_json() + "\n" + forged + "\n", encoding="utf-8")

    with pytest.raises(LedgerIntegrityError, match="hash mismatch"):
        retention_module.apply_retention_to_jsonl(target, RetentionPolicy(max_age_days=1), now=NOW)


def test_a_line_that_cannot_be_serialised_halts_with_a_typed_error_and_no_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    target = tmp_path / "audit.jsonl"
    target.write_text(chained("secret-id", GENESIS_PREV_HASH).to_json() + "\n", encoding="utf-8")

    class _Dumps:
        @staticmethod
        def dumps(data, **kwargs):
            if kwargs.get("allow_nan") is False:
                raise ValueError("Out of range float values are not JSON compliant")
            raise TypeError("not serialisable: secret-id")

    monkeypatch.setattr(retention_module, "json", _Dumps)

    with pytest.raises(LedgerIntegrityError, match=r"line 1 cannot be serialised \(TypeError\)") as caught:
        retention_module.verify_ledger(target)
    assert "secret-id" not in str(caught.value)


async def test_long_producer_id_is_cut_to_64_characters_in_the_integrity_message(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    long_id = "x" * 200
    first = chained("r0", GENESIS_PREV_HASH)
    forged = chained(long_id, "1" * 64)
    target.write_text(first.to_json() + "\n" + forged.to_json() + "\n", encoding="utf-8")

    with pytest.raises(LedgerIntegrityError) as excinfo:
        JsonlFileSink(target).verify()
    message = str(excinfo.value)
    assert "x" * 64 in message
    assert "x" * 65 not in message


@pytest.mark.parametrize("change", ["delete-last", "swap-last", "edit-middle"])
async def test_ledger_changed_between_the_two_passes_aborts_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
):
    target = tmp_path / "audit.jsonl"
    first = chained("old", GENESIS_PREV_HASH, timestamp=1.0)
    middle = chained("middle", first.hash, timestamp=NOW)
    last = chained("last", middle.hash, timestamp=NOW)
    target.write_text(lines([first, middle, last]), encoding="utf-8")
    before = target.read_text(encoding="utf-8")
    edited_middle = AuditRecord(
        id="middle", timestamp=NOW, source="s", event_type="e", attributes={"edited": True}
    ).with_hash_chain(prev_hash=first.hash)
    edited_last = chained("last", edited_middle.hash, timestamp=NOW)
    edited_text = lines([first, edited_middle, edited_last])

    real_scan = retention_module._scan
    passes: list[int] = []

    def _second_pass(fh, accepted, **kwargs):
        passes.append(1)
        second = len(passes) == 2
        if second and change == "edit-middle":
            target.write_text(edited_text, encoding="utf-8")
        items = list(real_scan(fh, accepted, **kwargs))
        if second and change == "delete-last":
            items = items[:-1]
        if second and change == "swap-last":
            line, record, age = items[-1]
            items[-1] = (line, dataclasses.replace(record, hash="e" * 64), age)
        yield from items

    monkeypatch.setattr(retention_module, "_scan", _second_pass)
    with pytest.raises(LedgerIntegrityError, match="changed during retention"):
        retention_module.apply_retention_to_jsonl(target, RetentionPolicy(max_age_days=1), now=NOW)

    assert target.read_text(encoding="utf-8") == (edited_text if change == "edit-middle" else before)
    assert read_anchor(target) is None
    assert not Path(f"{target}.tmp").exists()


async def test_ledger_tmp_sibling_is_removed_when_replace_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import os

    target = tmp_path / "audit.jsonl"
    await seed(target, [10, 0.5])
    original_replace = os.replace

    def _crash_on_ledger(src, dst, *args, **kwargs):
        if str(dst) == str(target):
            raise OSError("simulated")
        return original_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", _crash_on_ledger)
    with pytest.raises(OSError):
        await JsonlFileSink(target).apply_retention(RetentionPolicy(max_age_days=2), now=NOW)
    monkeypatch.undo()

    assert not Path(f"{target}.tmp").exists()


async def test_planted_symlink_at_ledger_tmp_is_removed_not_followed(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    await seed(target, [10, 0.5])
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me", encoding="utf-8")
    Path(f"{target}.tmp").symlink_to(victim)

    assert await JsonlFileSink(target).apply_retention(RetentionPolicy(max_age_days=2), now=NOW) == 1

    assert victim.read_text(encoding="utf-8") == "keep me"
    assert not Path(f"{target}.tmp").exists()
    assert ids(target) == ["r-1"]
    JsonlFileSink(target).verify()


async def test_stale_tmp_files_are_removed_even_when_nothing_is_pruned(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    await seed(target, [0.5])
    Path(f"{target}.tmp").write_text("stale partial snapshot", encoding="utf-8")
    Path(f"{anchor_path(target)}.tmp").write_text("stale anchor", encoding="utf-8")

    assert await JsonlFileSink(target).apply_retention(RetentionPolicy(max_age_days=2), now=NOW) == 0

    assert not Path(f"{target}.tmp").exists()
    assert not Path(f"{anchor_path(target)}.tmp").exists()


async def test_prune_fsyncs_the_snapshot_and_the_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import os

    from septum_audit import retention

    target = tmp_path / "audit.jsonl"
    await seed(target, [10, 0.5])
    synced_dirs: list[Path] = []
    real_fsync_dir = retention.fsync_dir
    monkeypatch.setattr(retention, "fsync_dir", lambda p: (synced_dirs.append(p), real_fsync_dir(p))[1])
    fsynced_fds: list[int] = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (fsynced_fds.append(fd), real_fsync(fd))[1])

    assert await JsonlFileSink(target).apply_retention(RetentionPolicy(max_age_days=2), now=NOW) == 1

    assert synced_dirs == [target]
    assert fsynced_fds


def test_a_timestamp_that_is_a_list_halts_retention_as_malformed(tmp_path: Path):
    from septum_audit import RetentionPolicy, apply_retention_to_jsonl
    from septum_audit.events import LedgerIntegrityError

    target = tmp_path / "audit.jsonl"
    target.write_text('{"id":"x","timestamp":[1],"source":"s","event_type":"e","attributes":{}}\n', encoding="utf-8")

    with pytest.raises(LedgerIntegrityError, match="malformed"):
        apply_retention_to_jsonl(target, RetentionPolicy(max_age_days=1), now=1_700_000_000.0)
    with pytest.raises(LedgerIntegrityError, match="malformed"):
        JsonlFileSink(target).verify()
