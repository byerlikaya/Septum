"""Characterization tests for ``update_spreadsheet_schema``.

Pins the ordered side effects of a schema update against in-memory fakes:
the pre-gate 422 refusals, which labels pass the PII gate and how the gated
values are written back, how new, matching and mismatched columns are
applied, the commit and the re-read the response is built from.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from septum_api.models.spreadsheet_schema import SpreadsheetColumn
from septum_api.request_limits import MAX_SCHEMA_LABELED_COLUMNS
from septum_api.routers import documents
from septum_api.routers.documents import (
    SpreadsheetColumnUpdate,
    SpreadsheetSchemaUpdatePayload,
)


def _col(index: int, tech: str, label: Any = None, numeric: Any = None) -> Any:
    return SimpleNamespace(index=index, technical_label=tech, semantic_label=label, is_numeric=numeric)


class _Db:
    def __init__(self, events: list[tuple]) -> None:
        self.events = events
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        self.events.append(("add", obj.index))

    async def commit(self) -> None:
        self.events.append(("commit",))


@pytest.fixture
def env(monkeypatch):
    events: list[tuple] = []
    state: dict[str, Any] = {
        "initial": None,
        "target": SimpleNamespace(id=31, columns=[]),
        "target_columns": {},
        "final": SimpleNamespace(columns=[_col(0, "COLUMN_1", "Name", False)]),
    }

    async def owned(db, model, document_id, user, detail):
        events.append(("owned", document_id, user, detail))
        return "DOC"

    async def read_schema(db, document_id, *, required=False):
        events.append(("read", document_id, required))
        return state["final"] if required else state["initial"]

    async def load_settings(db):
        events.append(("settings",))
        return "SETTINGS"

    async def gate(db, settings, document, texts):
        events.append(("gate", settings, document, list(texts)))
        return [f"G({t})" for t in texts]

    async def schema_for_update(db, document_id, payload):
        events.append(("schema_for_update", document_id, [c.semantic_label for c in payload.columns]))
        return state["target"], state["target_columns"]

    monkeypatch.setattr(documents, "get_owned_or_404", owned)
    monkeypatch.setattr(documents, "_read_schema", read_schema)
    monkeypatch.setattr(documents, "load_settings", load_settings)
    monkeypatch.setattr(documents, "gate_document_texts", gate)
    monkeypatch.setattr(documents, "_schema_for_update", schema_for_update)
    return SimpleNamespace(events=events, state=state)


def _payload(*cols: tuple) -> SpreadsheetSchemaUpdatePayload:
    return SpreadsheetSchemaUpdatePayload(
        columns=[
            SpreadsheetColumnUpdate(index=i, technical_label=t, semantic_label=s, is_numeric=n)
            for i, t, s, n in cols
        ]
    )


async def _update(env, payload):
    db = _Db(env.events)
    result = await documents.update_spreadsheet_schema(
        document_id=9, payload=payload, db=db, user="USER"
    )
    return result, db


async def test_new_schema_gates_labels_and_adds_columns(env):
    payload = _payload((1, "x", "Salary", True), (0, "y", None, None), (2, "z", "", False))
    result, db = await _update(env, payload)
    assert env.events == [
        ("owned", 9, "USER", "Document not found."),
        ("read", 9, False),
        ("settings",),
        ("gate", "SETTINGS", "DOC", ["Salary"]),
        ("schema_for_update", 9, ["G(Salary)", None, ""]),
        ("add", 1),
        ("add", 0),
        ("add", 2),
        ("commit",),
        ("read", 9, True),
    ]
    assert all(isinstance(c, SpreadsheetColumn) for c in db.added)
    assert [
        (c.schema_id, c.index, c.technical_label, c.semantic_label, c.is_numeric) for c in db.added
    ] == [
        (31, 1, "COLUMN_2", "G(Salary)", True),
        (31, 0, "COLUMN_1", None, None),
        (31, 2, "COLUMN_3", "", False),
    ]
    assert result.model_dump() == {
        "document_id": 9,
        "columns": [
            {"index": 0, "technical_label": "COLUMN_1", "semantic_label": "Name", "is_numeric": False}
        ],
    }


async def test_without_labels_the_gate_is_skipped(env):
    await _update(env, _payload((0, "COLUMN_1", None, True)))
    kinds = [e[0] for e in env.events]
    assert "settings" not in kinds and "gate" not in kinds


async def test_existing_columns_update_only_on_matching_technical_label(env):
    match = _col(0, "COLUMN_1", "Old", False)
    mismatch = _col(1, "COLUMN_2", "Keep", True)
    untouched = _col(2, "COLUMN_3", "Same", None)
    env.state["initial"] = SimpleNamespace(columns=[match, mismatch, untouched])
    env.state["target_columns"] = {0: match, 1: mismatch, 2: untouched}
    payload = _payload((0, "COLUMN_1", "New", True), (1, "WRONG", "Other", False))
    _, db = await _update(env, payload)
    assert db.added == []
    assert (match.semantic_label, match.is_numeric) == ("G(New)", True)
    assert (mismatch.semantic_label, mismatch.is_numeric) == ("Keep", True)
    assert (untouched.semantic_label, untouched.is_numeric) == ("Same", None)
    assert ("gate", "SETTINGS", "DOC", ["New", "Other"]) in env.events


async def test_matching_label_cleared_to_none(env):
    col = _col(0, "COLUMN_1", "Old", True)
    env.state["initial"] = SimpleNamespace(columns=[col])
    env.state["target_columns"] = {0: col}
    await _update(env, _payload((0, "COLUMN_1", None, None)))
    assert (col.semantic_label, col.is_numeric) == (None, None)


async def test_unknown_index_refused_before_gate(env):
    env.state["initial"] = SimpleNamespace(columns=[_col(0, "COLUMN_1")])
    with pytest.raises(HTTPException) as info:
        await _update(env, _payload((5, "COLUMN_6", "Label", None)))
    assert info.value.status_code == 422
    assert info.value.detail == "Column index is not part of the document's spreadsheet."
    assert [e[0] for e in env.events] == ["owned", "read"]


async def test_too_many_labels_refused_before_gate(env):
    cols = [_col(i, f"COLUMN_{i + 1}", f"L{i}") for i in range(MAX_SCHEMA_LABELED_COLUMNS)]
    cols.append(_col(MAX_SCHEMA_LABELED_COLUMNS, f"COLUMN_{MAX_SCHEMA_LABELED_COLUMNS + 1}"))
    env.state["initial"] = SimpleNamespace(columns=cols)
    last = MAX_SCHEMA_LABELED_COLUMNS
    with pytest.raises(HTTPException) as info:
        await _update(env, _payload((last, f"COLUMN_{last + 1}", "Extra", None)))
    assert info.value.status_code == 422
    assert info.value.detail == (
        f"At most {MAX_SCHEMA_LABELED_COLUMNS} columns may carry a semantic label."
    )
    assert [e[0] for e in env.events] == ["owned", "read"]
