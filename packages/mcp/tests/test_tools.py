from __future__ import annotations

"""Tests for :mod:`septum_mcp.tools`.

The tests exercise the pure-Python tool implementations directly
without spinning up an MCP transport. Each test uses the shared
``engine`` fixture (see ``conftest.py``) backed by the ``gdpr``
regulation pack with the NER transformer layer disabled.
"""

from pathlib import Path

from septum_core import SeptumEngine
from septum_mcp import tools


def test_mask_text_success(engine: SeptumEngine) -> None:
    result = tools.mask_text(engine, text="Email jane@example.com now.")

    assert result["ok"] is True
    data = result["data"]
    assert "[EMAIL_ADDRESS_1]" in data["masked_text"]
    assert "jane@example.com" not in data["masked_text"]
    assert data["entity_count"] == 1
    assert data["entity_type_counts"] == {"EMAIL_ADDRESS": 1}
    assert data["session_id"]


def test_mask_text_rejects_empty_input(engine: SeptumEngine) -> None:
    result = tools.mask_text(engine, text="   ")

    assert result["ok"] is False
    assert "non-empty" in result["error"]


def test_mask_text_rejects_non_string_input(engine: SeptumEngine) -> None:
    result = tools.mask_text(engine, text=42)

    assert result["ok"] is False


def test_unmask_response_round_trips(engine: SeptumEngine) -> None:
    masked = tools.mask_text(engine, text="Email jane@example.com now.")
    session_id = masked["data"]["session_id"]

    restored = tools.unmask_response(
        engine,
        text="Reply to [EMAIL_ADDRESS_1] was sent.",
        session_id=session_id,
    )

    assert restored["ok"] is True
    assert restored["data"]["text"] == "Reply to jane@example.com was sent."


def test_unmask_response_errors_for_unknown_session(engine: SeptumEngine) -> None:
    result = tools.unmask_response(
        engine, text="[EMAIL_ADDRESS_1]", session_id="missing"
    )

    assert result["ok"] is False
    assert "not found" in result["error"].lower()


def test_unmask_response_requires_non_empty_text(engine: SeptumEngine) -> None:
    masked = tools.mask_text(engine, text="Email jane@example.com now.")
    result = tools.unmask_response(
        engine, text="", session_id=masked["data"]["session_id"]
    )

    assert result["ok"] is False


def test_detect_pii_returns_entities_without_session(engine: SeptumEngine) -> None:
    result = tools.detect_pii(engine, text="Contact jane@example.com today.")

    assert result["ok"] is True
    entities = result["data"]["entities"]
    assert len(entities) == 1
    entity = entities[0]
    assert entity["entity_type"] == "EMAIL_ADDRESS"
    assert entity["placeholder"] == "[EMAIL_ADDRESS_1]"
    assert entity["start"] < entity["end"]
    assert 0.0 <= entity["score"] <= 1.0


def test_detect_pii_does_not_retain_session(engine: SeptumEngine) -> None:
    before = engine.active_session_count()
    tools.detect_pii(engine, text="Email jane@example.com now.")
    after = engine.active_session_count()

    assert after == before


def test_scan_file_detect_only(tmp_path: Path, engine: SeptumEngine) -> None:
    target = tmp_path / "note.txt"
    target.write_text("Contact jane@example.com today.", encoding="utf-8")

    result = tools.scan_file(engine, file_path=str(target), mask=False)

    assert result["ok"] is True
    data = result["data"]
    assert data["format"] == "txt"
    assert data["entity_count"] == 1
    assert "masked_text" not in data
    assert "session_id" not in data


def test_scan_file_with_mask_returns_session(tmp_path: Path, engine: SeptumEngine) -> None:
    target = tmp_path / "note.txt"
    target.write_text("Contact jane@example.com today.", encoding="utf-8")

    result = tools.scan_file(engine, file_path=str(target), mask=True)

    assert result["ok"] is True
    data = result["data"]
    assert "[EMAIL_ADDRESS_1]" in data["masked_text"]
    assert data["session_id"]

    restored = tools.unmask_response(
        engine, text="Contact [EMAIL_ADDRESS_1] today.", session_id=data["session_id"]
    )
    assert restored["ok"] is True
    assert restored["data"]["text"] == "Contact jane@example.com today."


def test_scan_file_missing_path(engine: SeptumEngine) -> None:
    result = tools.scan_file(engine, file_path="/does/not/exist.txt")

    assert result["ok"] is False
    assert "not found" in result["error"].lower()


def test_scan_file_empty_file(tmp_path: Path, engine: SeptumEngine) -> None:
    target = tmp_path / "empty.txt"
    target.write_text("", encoding="utf-8")

    result = tools.scan_file(engine, file_path=str(target))

    assert result["ok"] is False
    assert "empty" in result["error"].lower()


def test_list_regulations_marks_active_packs() -> None:
    result = tools.list_regulations(active_regulations=["gdpr", "kvkk"])

    assert result["ok"] is True
    packs = result["data"]["regulations"]
    by_id = {p["id"]: p for p in packs}
    assert by_id["gdpr"]["is_active"] is True
    assert by_id["kvkk"]["is_active"] is True
    assert by_id["hipaa"]["is_active"] is False
    # sanity-check: packs declare at least one entity type
    assert by_id["gdpr"]["entity_types"]


def test_list_regulations_without_active_set_marks_all_inactive() -> None:
    result = tools.list_regulations()

    assert result["ok"] is True
    assert all(not p["is_active"] for p in result["data"]["regulations"])
    assert result["data"]["active_regulation_ids"] == []


def test_get_session_map_returns_entries(engine: SeptumEngine) -> None:
    masked = tools.mask_text(engine, text="Email jane@example.com now.")
    session_id = masked["data"]["session_id"]

    result = tools.get_session_map(engine, session_id=session_id)

    assert result["ok"] is True
    entries = result["data"]["entries"]
    assert entries == [
        {"original": "jane@example.com", "placeholder": "[EMAIL_ADDRESS_1]"}
    ]
    assert result["data"]["entry_count"] == 1


def test_get_session_map_errors_for_unknown_session(engine: SeptumEngine) -> None:
    result = tools.get_session_map(engine, session_id="missing")

    assert result["ok"] is False
    assert "not found" in result["error"].lower()


def test_get_session_map_requires_string_session_id(engine: SeptumEngine) -> None:
    result = tools.get_session_map(engine, session_id=None)

    assert result["ok"] is False
