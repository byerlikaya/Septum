"""Torn tails on the JSONL ledger: a complete record is restored, anything else is truncated, and the cached head follows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from septum_audit import AuditRecord, JsonlFileSink, RetentionPolicy
from septum_audit import sink as sink_module
from septum_audit.anchor import RetentionAnchor, write_anchor
from septum_audit.events import GENESIS_PREV_HASH
from septum_audit.sink import _last_newline_before

from _helpers import NOW, append, chain, chained, ids, ledger_from_text, ledger_with_tail, put, seed


async def test_torn_but_valid_tail_keeps_its_record_and_gets_its_newline(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    sink = JsonlFileSink(target)
    await put(sink, AuditRecord(id="first", source="s", event_type="e"))
    head = list(sink.read_all())[-1]
    complete = chained("second", head.hash)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(complete.to_json())

    await put(sink, AuditRecord(id="third", source="s", event_type="e"))

    assert ids(target) == ["first", "second", "third"]
    sink.verify()


async def test_partial_line_that_chains_to_nothing_is_truncated(tmp_path: Path):
    target, sink = await ledger_with_tail(tmp_path, '{"id":"tor')

    await append(sink, "second")

    assert ids(target) == ["first", "second"]
    sink.verify()


@pytest.mark.parametrize(
    "fragment",
    ["wrong-link", "wrong-hash", "non-canonical", "stray-from-genesis", "unknown-field", "attributes-not-object"],
)
async def test_complete_fragment_that_fails_a_check_is_truncated(tmp_path: Path, fragment: str):
    r0 = chained("r0", GENESIS_PREV_HASH)
    r1 = chained("r1", r0.hash)
    tampered = json.loads(r1.to_json())
    tampered["source"] = "tampered"
    smuggled = json.loads(r1.to_json())
    smuggled["smuggled"] = 1
    text = {
        "wrong-link": lambda: chained("orphan", "f" * 64).to_json(),
        "wrong-hash": lambda: json.dumps(tampered, separators=(",", ":")),
        "non-canonical": lambda: json.dumps(json.loads(r1.to_json())),
        "stray-from-genesis": lambda: chained("stray", GENESIS_PREV_HASH).to_json(),
        "unknown-field": lambda: json.dumps(smuggled, separators=(",", ":")),
        "attributes-not-object": lambda: '{"id":"x","timestamp":1.0,"attributes":5}',
    }[fragment]()
    target, sink = ledger_from_text(tmp_path, r0.to_json() + "\n" + text)

    await append(sink, "after")

    assert ids(target) == ["r0", "after"]
    sink.verify()


async def test_oversized_but_complete_fragment_is_truncated_without_being_parsed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    r0 = chained("r0", GENESIS_PREV_HASH)
    big = AuditRecord(id="big", timestamp=NOW, source="s", event_type="e", attributes={"p": "x" * ((1 << 20) + 64)})
    big = big.with_hash_chain(prev_hash=r0.hash)
    target, sink = ledger_from_text(tmp_path, r0.to_json() + "\n" + big.to_json())
    calls: list[int] = []
    real = sink_module._fragment_chains
    monkeypatch.setattr(sink_module, "_fragment_chains", lambda *a: calls.append(1) or real(*a))

    await append(sink, "r1")

    assert calls == []
    assert ids(target) == ["r0", "r1"]
    sink.verify()


async def test_deep_nesting_in_a_torn_fragment_is_truncated_and_appends_still_work(tmp_path: Path):
    r0 = chained("r0", GENESIS_PREV_HASH)
    target, sink = ledger_from_text(tmp_path, r0.to_json() + "\n" + '{"id":"deep","attributes":' + "[" * 200_000)

    await append(sink, "after")

    assert ids(target) == ["r0", "after"]
    JsonlFileSink(target).verify()


async def test_complete_fragment_linking_to_its_predecessor_is_restored_without_a_newline(tmp_path: Path):
    records = chain(1)
    target, _ = ledger_from_text(tmp_path, records[0].to_json())

    await append(JsonlFileSink(target), "next")

    sink = JsonlFileSink(target)
    sink.verify()
    assert ids(target) == ["r-0", "next"]
    assert b"\n\n" not in target.read_bytes()


async def test_unterminated_first_record_on_a_pruned_ledger_links_to_the_anchor(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    records = chain(2)
    write_anchor(
        target,
        RetentionAnchor(hash=records[0].hash, previous_hash=GENESIS_PREV_HASH, removed_total=1, pruned_at=NOW),
    )
    target.write_text(records[1].to_json(), encoding="utf-8")

    await append(JsonlFileSink(target), "next")

    sink = JsonlFileSink(target)
    sink.verify()
    assert ids(target) == ["r-1", "next"]


async def test_torn_tail_after_a_blank_line_is_restored_against_the_last_hashed_line(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    r0 = chained("r0", GENESIS_PREV_HASH)
    r1 = chained("r1", r0.hash)
    target.write_text(r0.to_json() + "\n\n" + r1.to_json(), encoding="utf-8")

    sink = JsonlFileSink(target)
    await append(sink, "r2")

    assert ids(target) == ["r0", "r1", "r2"]
    sink.verify()


async def test_complete_fragment_linking_to_its_predecessor_across_junk_blocks_is_restored(tmp_path: Path):
    r0 = chained("r0", GENESIS_PREV_HASH)
    r1 = chained("r1", r0.hash)
    junk = "{" + "j" * 9000
    target, sink = ledger_from_text(tmp_path, r0.to_json() + "\n" + junk + "\n\n" + r1.to_json())

    await append(sink, "r2")

    assert [r.id for r in JsonlFileSink(target).read_all()] == ["r0", "r1", "r2"]


async def test_non_utf8_torn_tail_is_dropped_as_residue(tmp_path: Path):
    from septum_audit import retention as retention_module

    target = tmp_path / "audit.jsonl"
    old = chained("old", GENESIS_PREV_HASH, timestamp=1.0)
    target.write_bytes(old.to_json().encode() + b"\n\xff\xfe torn")

    assert retention_module.apply_retention_to_jsonl(target, RetentionPolicy(max_age_days=1), now=NOW) == 2
    assert target.read_text(encoding="utf-8") == ""


async def test_dropping_a_torn_tail_clears_a_stale_chain_head(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    first = JsonlFileSink(target)
    await append(first, "a")
    await append(JsonlFileSink(target), "b")
    with open(target, "ab") as fh:
        fh.write(b'{"id": "torn')

    await append(first, "c")

    first.verify()
    assert [r.id for r in first.read_all()] == ["a", "b", "c"]


async def test_torn_only_retention_pass_refreshes_the_cached_head(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    first = JsonlFileSink(target)
    await append(first, "a")
    await append(JsonlFileSink(target), "b")
    with open(target, "ab") as fh:
        fh.write(b'{"id": "torn')

    outcome = await first.apply_retention_outcome(RetentionPolicy(max_age_days=3650))
    assert (outcome.pruned, outcome.torn) == (0, 1)

    await append(first, "c")
    first.verify()
    assert [r.id for r in first.read_all()] == ["a", "b", "c"]


async def test_noop_policy_leaves_a_torn_tail_alone(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    sink = JsonlFileSink(target)
    await append(sink, "a")
    with open(target, "ab") as fh:
        fh.write(b'{"id": "torn')
    before = target.read_bytes()

    outcome = await sink.apply_retention_outcome(RetentionPolicy())

    assert (outcome.pruned, outcome.torn, outcome.head) == (0, 0, None)
    assert target.read_bytes() == before


async def test_torn_tail_is_dropped_before_the_next_append_so_retention_is_not_halted(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    await seed(target, [10, 0.5])
    with target.open("a", encoding="utf-8") as fh:
        fh.write('{"id":"tor')
    sink = JsonlFileSink(target)

    await put(sink, AuditRecord(id="after-crash", timestamp=NOW, source="s", event_type="e"))

    assert ids(target) == ["r-0", "r-1", "after-crash"]
    sink.verify()
    assert await sink.apply_retention(RetentionPolicy(max_age_days=2), now=NOW) == 1
    assert ids(target) == ["r-1", "after-crash"]


async def test_torn_trailing_line_is_pruned_and_chain_still_verifies(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    await seed(target, [10, 0.5, 0.2])
    with target.open("a", encoding="utf-8") as fh:
        fh.write("not-json")

    assert await JsonlFileSink(target).apply_retention(RetentionPolicy(max_age_days=2), now=NOW) == 2

    assert ids(target) == ["r-1", "r-2"]
    JsonlFileSink(target).verify()


def test_last_newline_before_finds_a_newline_on_a_block_boundary():
    import io

    data = b"x" * 5 + b"\n" + b"y" * 4095
    assert len(data) == 5 + 4096
    assert _last_newline_before(io.BytesIO(data), len(data)) == 5


def test_last_newline_before_scans_across_blocks_and_reports_none():
    import io

    data = b"a\n" + b"y" * 10_000
    assert _last_newline_before(io.BytesIO(data), len(data)) == 1
    assert _last_newline_before(io.BytesIO(b"y" * 10_000), 10_000) == -1
