"""unmask_response restores only placeholders the session emitted, under a per-session budget."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from septum_core import SeptumEngine
from septum_mcp import tools
from septum_mcp.config import MCPConfig

THREE_EMAILS = "Mail a@example.com, b@example.com and c@example.com today."
FOUR_EMAILS = "Mail a@example.com, b@example.com, c@example.com and d@example.com today."


def test_emitted_placeholder_restores(engine: SeptumEngine) -> None:
    masked = tools.mask_text(engine, text="Email jane@example.com now.")
    sid = masked["data"]["session_id"]

    result = tools.unmask_response(engine, text="Hi [EMAIL_ADDRESS_1]", session_id=sid)

    assert result["ok"] is True
    assert result["data"]["text"] == "Hi jane@example.com"


def test_map_entry_never_emitted_stays_literal(engine: SeptumEngine) -> None:
    masked = tools.mask_text(engine, text="Email jane@example.com now.")
    sid = masked["data"]["session_id"]
    hidden = engine._sessions[sid].add_entity("secret@example.com", "EMAIL_ADDRESS")
    assert hidden not in masked["data"]["masked_text"]

    result = tools.unmask_response(
        engine, text=f"[EMAIL_ADDRESS_1] {hidden} [PERSON_NAME_1]", session_id=sid
    )

    assert result["ok"] is True
    assert result["data"]["text"] == f"jane@example.com {hidden} [PERSON_NAME_1]"
    assert "secret@example.com" not in result["data"]["text"]


def test_session_that_never_masked_restores_nothing(engine: SeptumEngine) -> None:
    direct = engine.mask("Email jane@example.com now.")

    result = tools.unmask_response(
        engine, text="Hi [EMAIL_ADDRESS_1]", session_id=direct.session_id
    )

    assert result["ok"] is True
    assert result["data"]["text"] == "Hi [EMAIL_ADDRESS_1]"


def test_scan_file_mask_emits_placeholders(tmp_path: Path, engine: SeptumEngine) -> None:
    target = tmp_path / "note.txt"
    target.write_text("Contact jane@example.com today.", encoding="utf-8")
    sid = tools.scan_file(engine, file_root=None, file_path=str(target), mask=True)["data"]["session_id"]

    result = tools.unmask_response(engine, text="[EMAIL_ADDRESS_1]", session_id=sid)

    assert result["data"]["text"] == "jane@example.com"


def test_cap_refuses_without_restoring(engine: SeptumEngine) -> None:
    masked = tools.mask_text(engine, text=THREE_EMAILS)
    assert masked["data"]["entity_count"] == 3
    sid = masked["data"]["session_id"]
    reply = "[EMAIL_ADDRESS_1] [EMAIL_ADDRESS_2] [EMAIL_ADDRESS_3]"

    refused = tools.unmask_response(engine, text=reply, session_id=sid, max_placeholders=2)
    allowed = tools.unmask_response(engine, text=reply, session_id=sid, max_placeholders=3)

    assert refused["ok"] is False
    assert "cap of 2" in refused["error"]
    assert "@example.com" not in refused["error"]
    assert allowed["ok"] is True
    assert allowed["data"]["text"] == "a@example.com b@example.com c@example.com"


def test_cap_counts_distinct_placeholders(engine: SeptumEngine) -> None:
    sid = tools.mask_text(engine, text=THREE_EMAILS)["data"]["session_id"]

    result = tools.unmask_response(
        engine,
        text="[EMAIL_ADDRESS_1] [EMAIL_ADDRESS_1] [EMAIL_ADDRESS_1]",
        session_id=sid,
        max_placeholders=1,
    )

    assert result["ok"] is True


def test_released_sessions_are_pruned_from_ledger(engine: SeptumEngine) -> None:
    first = tools.mask_text(engine, text="Email jane@example.com now.")["data"]["session_id"]
    engine.release(first)
    second = tools.mask_text(engine, text="Email joe@example.com now.")["data"]["session_id"]

    ledger = tools._ledger_for(engine)
    assert ledger.emitted(first) == frozenset()
    assert ledger.emitted(second) == frozenset({"[EMAIL_ADDRESS_1]"})


def test_config_cap_default_and_override() -> None:
    assert MCPConfig.from_env({}).max_unmask_placeholders == 25
    env = {"SEPTUM_MCP_MAX_UNMASK_PLACEHOLDERS": "5"}
    assert MCPConfig.from_env(env).max_unmask_placeholders == 5
    for bad in ("0", "-3", "abc", " "):
        env = {"SEPTUM_MCP_MAX_UNMASK_PLACEHOLDERS": bad}
        assert MCPConfig.from_env(env).max_unmask_placeholders == 25


def test_unmask_refreshes_session_idle_ttl(engine: SeptumEngine) -> None:
    masked = tools.mask_text(engine, text="Email jane@example.com now.")
    sid = masked["data"]["session_id"]
    engine._session_expiry[sid] = time.monotonic() + 1

    result = tools.unmask_response(engine, text="Hi [EMAIL_ADDRESS_1]", session_id=sid)

    assert result["data"]["text"] == "Hi jane@example.com"
    assert engine._session_expiry[sid] > time.monotonic() + engine._session_ttl_seconds / 2


def test_cap_is_per_session_so_split_calls_are_refused(engine: SeptumEngine) -> None:
    sid = tools.mask_text(engine, text=THREE_EMAILS)["data"]["session_id"]

    first = tools.unmask_response(
        engine, text="[EMAIL_ADDRESS_1] [EMAIL_ADDRESS_2]", session_id=sid, max_placeholders=2
    )
    second = tools.unmask_response(
        engine, text="[EMAIL_ADDRESS_3]", session_id=sid, max_placeholders=2
    )

    assert first["ok"] is True
    assert second["ok"] is False
    assert "per-session cap of 2" in second["error"]
    assert "split" not in second["error"].lower()
    assert "@example.com" not in second["error"]
    assert tools._ledger_for(engine).restored(sid) == frozenset(
        {"[EMAIL_ADDRESS_1]", "[EMAIL_ADDRESS_2]"}
    )


def test_refused_call_consumes_no_budget(engine: SeptumEngine) -> None:
    sid = tools.mask_text(engine, text=THREE_EMAILS)["data"]["session_id"]
    reply = "[EMAIL_ADDRESS_1] [EMAIL_ADDRESS_2] [EMAIL_ADDRESS_3]"

    assert tools.unmask_response(engine, text=reply, session_id=sid, max_placeholders=2)["ok"] is False
    single = tools.unmask_response(
        engine, text="[EMAIL_ADDRESS_3]", session_id=sid, max_placeholders=2
    )

    assert single["data"]["text"] == "c@example.com"


def test_re_restoring_a_placeholder_is_free(engine: SeptumEngine) -> None:
    sid = tools.mask_text(engine, text=THREE_EMAILS)["data"]["session_id"]

    results = [
        tools.unmask_response(
            engine, text="Again [EMAIL_ADDRESS_1]", session_id=sid, max_placeholders=1
        )
        for _ in range(5)
    ]

    assert all(r["ok"] for r in results)
    assert results[-1]["data"]["text"] == "Again a@example.com"
    assert tools._ledger_for(engine).restored(sid) == frozenset({"[EMAIL_ADDRESS_1]"})


def test_budget_is_isolated_between_sessions(engine: SeptumEngine) -> None:
    first = tools.mask_text(engine, text="Email jane@example.com now.")["data"]["session_id"]
    second = tools.mask_text(engine, text="Email joe@example.com now.")["data"]["session_id"]

    assert tools.unmask_response(engine, text="[EMAIL_ADDRESS_1]", session_id=first, max_placeholders=1)["ok"]
    result = tools.unmask_response(
        engine, text="[EMAIL_ADDRESS_1]", session_id=second, max_placeholders=1
    )

    assert result["data"]["text"] == "joe@example.com"


def _per_placeholder_count(engine: SeptumEngine, text: str, sid: str, emitted) -> set:
    """The previous O(E) semantics: one unmask per emitted placeholder."""
    return {
        p
        for p in emitted
        if engine.unmask(text, sid, allowed_placeholders={p}) != text
    }


@pytest.mark.parametrize(
    "reply",
    [
        "Hello [PERSON_1] and [PERSON_NAME_2].",
        "[ORGANIZATION_1] hired [PERSON_NAME_1]",
        "Short self-mapped [PERSON_1] stays its own placeholder",
        "Nested [[PERSON_NAME_2]] and glued [EMAIL_ADDRESS_1][PERSON_2]",
        "Unknown [PERSON_9] [ORGANIZATION_NAME_7] [EMAIL_ADDRESS_2]",
        "No placeholders at all.",
        "[ORGANIZATION_NAME_1",
    ],
)
def test_single_pass_count_matches_per_placeholder_unmask(
    engine: SeptumEngine, reply: str
) -> None:
    sid = engine.mask("Email jane@example.com now.").session_id
    anon_map = engine._sessions[sid]
    anon_map.add_entity("Jane Doe", "PERSON_NAME")
    anon_map.add_entity("John Roe", "PERSON_NAME")
    anon_map.add_entity("Acme Corp", "ORGANIZATION_NAME")
    anon_map.add_entity("Self Mapped", "PERSON")
    mapping = engine.get_session_map(sid)
    emitted = frozenset(mapping.values())

    single_pass = tools._placeholders_restored_by(reply, emitted, mapping.values())

    assert single_pass == _per_placeholder_count(engine, reply, sid, emitted)


def test_single_pass_count_ignores_aliases_of_unemitted_placeholders(
    engine: SeptumEngine,
) -> None:
    sid = engine.mask("Email jane@example.com now.").session_id
    engine._sessions[sid].add_entity("Jane Doe", "PERSON_NAME")
    mapping = engine.get_session_map(sid)

    hits = tools._placeholders_restored_by(
        "[PERSON_1] [EMAIL_ADDRESS_1]", frozenset({"[EMAIL_ADDRESS_1]"}), mapping.values()
    )

    assert hits == frozenset({"[EMAIL_ADDRESS_1]"})


def test_single_pass_count_follows_short_alias(engine: SeptumEngine) -> None:
    sid = engine.mask("Email jane@example.com now.").session_id
    engine._sessions[sid].add_entity("Jane Doe", "PERSON_NAME")
    engine._sessions[sid].add_entity("Acme Corp", "ORGANIZATION_NAME")
    mapping = engine.get_session_map(sid)

    hits = tools._placeholders_restored_by(
        "[PERSON_1] works at [ORGANIZATION_1]", frozenset(mapping.values()), mapping.values()
    )

    assert hits == frozenset({"[PERSON_NAME_1]", "[ORGANIZATION_NAME_1]"})


def test_budget_exact_boundary(engine: SeptumEngine) -> None:
    sid = tools.mask_text(engine, text=FOUR_EMAILS)["data"]["session_id"]

    first = tools.unmask_response(engine, text="[EMAIL_ADDRESS_1]", session_id=sid, max_placeholders=3)
    at_cap = tools.unmask_response(
        engine, text="[EMAIL_ADDRESS_1] [EMAIL_ADDRESS_2] [EMAIL_ADDRESS_3]",
        session_id=sid, max_placeholders=3,
    )
    over_cap = tools.unmask_response(engine, text="[EMAIL_ADDRESS_4]", session_id=sid, max_placeholders=3)

    assert first["ok"] is True
    assert at_cap["ok"] is True
    assert at_cap["data"]["text"] == "a@example.com b@example.com c@example.com"
    assert over_cap["ok"] is False
    assert "restore 4 distinct" in over_cap["error"]
    assert tools._ledger_for(engine).restored(sid) == frozenset(
        {"[EMAIL_ADDRESS_1]", "[EMAIL_ADDRESS_2]", "[EMAIL_ADDRESS_3]"}
    )


def test_concurrent_consume_never_exceeds_cap() -> None:
    ledger = tools._EmittedPlaceholderLedger()
    cap = 5
    workers = 32
    barrier = threading.Barrier(workers)
    outcomes: list = []
    outcomes_lock = threading.Lock()

    def worker(index: int) -> None:
        barrier.wait(timeout=5)
        outcome = ledger.consume("sid", frozenset({f"[EMAIL_ADDRESS_{index}]"}), cap)
        with outcomes_lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(outcomes) == workers
    assert sum(1 for o in outcomes if o is None) == cap
    assert len(ledger.restored("sid")) == cap


def test_restored_original_spelling_a_placeholder_is_not_restored_uncounted(
    engine: SeptumEngine,
) -> None:
    """Sequential replacement must not restore a placeholder the budget never charged."""
    sid = tools.mask_text(engine, text="Mail a@example.com and b@example.com today.")["data"][
        "session_id"
    ]
    anon_map = engine._sessions[sid]
    crafted = "see [EMAIL_ADDRESS_2]"
    anon_map.entity_map = {crafted: "[EMAIL_ADDRESS_1]", "b@example.com": "[EMAIL_ADDRESS_2]"}
    anon_map.placeholder_lookup = {}

    result = tools.unmask_response(
        engine, text="[EMAIL_ADDRESS_1]", session_id=sid, max_placeholders=1
    )

    assert result["ok"] is True
    assert "b@example.com" not in result["data"]["text"]
    assert result["data"]["text"] == crafted
    assert tools._ledger_for(engine).restored(sid) == frozenset({"[EMAIL_ADDRESS_1]"})
