from __future__ import annotations

"""Spreadsheet column labels follow the turn's unified placeholder numbering.

A typed label is stored masked in its document's numbering. A chat turn
renumbers every chunk into one unified space, which can swap or compact
numbers; the label must be renumbered the same way, so the placeholder the
cloud model reads and the name the owner sees back are the person the label
meant. A placeholder the document map does not hold restores to nobody.
The cloud LLM is mocked: it echoes the label of column 0.
"""

import re
from typing import Any, Optional

import pytest
from chat_harness import ChatEnv
from septum_api.models.spreadsheet_schema import SpreadsheetColumn, SpreadsheetSchema
from septum_api.routers import chat as chat_module
from septum_api.services import document_anon_store
from septum_api.services.anonymization_map import AnonymizationMap
from septum_api.services.approval_gate import ApprovalChunk, ApprovalDecision
from septum_api.services.deanonymizer import Deanonymizer
from septum_api.services.llm_router import LLMRouter

_LABEL_LINE = re.compile(r"index 0 → (.+?)(?:; index \d|\.\n)")
_PLACEHOLDER = re.compile(r"\[[A-Z][A-Z_]*_\d+\]")


@pytest.fixture
async def env(chat_env: ChatEnv, tmp_path, monkeypatch: pytest.MonkeyPatch) -> ChatEnv:
    """``chat_env`` with the real document map store and de-anonymizer."""
    monkeypatch.setattr(document_anon_store, "_ANON_MAP_DIR", tmp_path / "maps")
    monkeypatch.setattr(document_anon_store, "_document_maps", {})
    monkeypatch.setattr(chat_module, "get_document_map", document_anon_store.get_document_map)
    monkeypatch.setattr(chat_module, "Deanonymizer", Deanonymizer)

    async def _echo_label(self, messages, **kwargs):
        prompt = messages[-1]["content"]
        chat_env.prompts.append(prompt)
        match = _LABEL_LINE.search(prompt)
        return match.group(1) if match else "no label"

    monkeypatch.setattr(LLMRouter, "complete", _echo_label)
    chat_env.act_as("alice")
    return chat_env


class _ApprovingGate:
    """Approval gate double that approves the offered chunks unchanged."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> None:
        self.created.append(kwargs)

    def discard_session(self, session_id: str, handle: Any) -> None:
        pass

    async def wait_for_approval(self, session_id: str, timeout: float) -> ApprovalDecision:
        texts = self.created[-1]["masked_chunks"]
        return ApprovalDecision(
            approved=True, chunks=[ApprovalChunk(id=None, document_id=None, text=t) for t in texts]
        )


async def _document(
    env: ChatEnv,
    chunk: str,
    names: list[Optional[str]],
    labels: list[str],
    reserve: int = 0,
) -> int:
    """Store a document, its map (``None`` in ``names`` reserves up to ``reserve``) and labels."""
    doc = await env.add_document(env.current_user, "sheet.xlsx", [chunk])
    amap = AnonymizationMap(document_id=doc, language="en")
    for name in names:
        if name is None:
            amap.token_counter["PERSON_NAME"] = reserve
        else:
            amap.add_entity(name, "PERSON_NAME")
    await document_anon_store.set_document_map(doc, amap)
    async with env.session_maker() as session:
        schema = SpreadsheetSchema(document_id=doc)
        session.add(schema)
        await session.flush()
        for index, label in enumerate(labels):
            session.add(
                SpreadsheetColumn(
                    schema_id=schema.id,
                    index=index,
                    technical_label=f"COLUMN_{index + 1}",
                    semantic_label=label,
                    is_numeric=False,
                )
            )
        await session.commit()
    return doc


def _answer(events: list[dict[str, Any]]) -> str:
    assert not [e for e in events if e["type"] == "error"], events
    return "".join(e["text"] for e in events if e["type"] == "answer_chunk")


async def _ask(env: ChatEnv, payload: dict[str, Any]) -> list[dict[str, Any]]:
    status, events, body = await env.ask(payload)
    assert status == 200, body
    return events


def _sent_label(env: ChatEnv) -> str:
    match = _LABEL_LINE.search(env.prompts[-1])
    assert match is not None, env.prompts[-1]
    return match.group(1)


def _placeholder_before(prompt: str, word: str) -> str:
    match = re.search(r"(\[[A-Z][A-Z_]*_\d+\]) " + word, prompt)
    assert match is not None, prompt
    return match.group(1)


async def _swapped_document(env: ChatEnv) -> int:
    """Map ``{Ali: _1, Ahmet Kaya: _2}``: the unified space swaps the two numbers."""
    return await _document(
        env,
        "[PERSON_NAME_1] paid; [PERSON_NAME_2] signed.",
        ["Ali", "Ahmet Kaya"],
        ["[PERSON_NAME_1] bonus"],
    )


async def test_swapped_numbers_label_restores_its_own_person(env: ChatEnv) -> None:
    await env.set_require_approval(False)
    doc = await _swapped_document(env)

    answer = _answer(await _ask(env, {"message": "Whose bonus?", "document_ids": [doc]}))

    prompt = env.prompts[-1]
    assert _sent_label(env) == f"{_placeholder_before(prompt, 'paid')} bonus"
    assert answer == "Ali bonus"


async def test_compacted_numbers_leave_an_unknown_label_placeholder_inert(env: ChatEnv) -> None:
    await env.set_require_approval(False)
    doc = await _document(
        env,
        "[PERSON_NAME_1] and [PERSON_NAME_2] signed. [PERSON_NAME_4] notarised.",
        ["Jane Roe", "Carl Doe", None, "Bob Jones"],
        ["[PERSON_NAME_3] bonus"],
        reserve=3,
    )

    answer = _answer(await _ask(env, {"message": "Whose bonus?", "document_ids": [doc]}))

    prompt = env.prompts[-1]
    label_placeholder = _PLACEHOLDER.search(_sent_label(env)).group(0)
    assert label_placeholder != _placeholder_before(prompt, "notarised")
    for name in ("Bob Jones", "Jane Roe", "Carl Doe"):
        assert name not in answer
    assert answer == f"{label_placeholder} bonus"


async def test_single_document_identity_numbering_keeps_labels_as_stored(env: ChatEnv) -> None:
    await env.set_require_approval(False)
    doc = await _document(
        env,
        "[PERSON_NAME_1] earns 10.",
        ["Alice Smith"],
        ["[PERSON_NAME_1] bonus", "Total amount"],
    )

    answer = _answer(await _ask(env, {"message": "Whose bonus?", "document_ids": [doc]}))

    prompt = env.prompts[-1]
    assert "index 0 → [PERSON_NAME_1] bonus" in prompt
    assert "index 1 → Total amount" in prompt
    assert answer == "Alice Smith bonus"


@pytest.mark.parametrize("lead", ["first", "second"])
async def test_colliding_numbers_across_documents_restore_the_lead_documents_person(
    env: ChatEnv, lead: str
) -> None:
    await env.set_require_approval(False)
    first = await _document(
        env,
        "[PERSON_NAME_1] owns it; [PERSON_NAME_2] witnessed.",
        ["Alice Smith", "Carl Doe"],
        ["[PERSON_NAME_1] bonus"],
    )
    second = await _swapped_document(env)
    order, expected = ([first, second], "Alice Smith") if lead == "first" else (
        [second, first],
        "Ali",
    )

    answer = _answer(await _ask(env, {"message": "Whose bonus?", "document_ids": order}))

    assert answer == f"{expected} bonus"


async def test_pre_approved_retry_renumbers_the_label_too(
    env: ChatEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    await env.set_require_approval(True)
    doc = await _swapped_document(env)
    events = await _ask(env, {"message": "Whose bonus?", "document_ids": [doc]})
    approval = next(e for e in events if e["type"] == "approval_required")
    monkeypatch.setattr(chat_module, "get_approval_gate", lambda: _ApprovingGate())

    answer = _answer(
        await _ask(
            env,
            {
                "message": "Whose bonus?",
                "document_ids": [doc],
                "pre_approved_chunks": [
                    {"text": c["text"], "document_id": c["document_id"]}
                    for c in approval["chunks"]
                ],
                "numbering_token": approval["numbering_token"],
            },
        )
    )

    prompt = env.prompts[-1]
    assert _sent_label(env) == f"{_placeholder_before(prompt, 'paid')} bonus"
    assert answer == "Ali bonus"
