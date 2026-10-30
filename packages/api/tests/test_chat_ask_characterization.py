"""Characterization tests for the ``/api/chat/ask`` stream.

They pin what the chat turn emits today: the exact SSE event sequence and
payloads, the prompt the (mocked) LLM receives, the audit events, the error
log calls and the router's own log lines. Every LLM, retrieval, sanitizer
and approval-gate call is a test double; no provider is ever contacted.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import pytest
from chat_harness import ChatEnv
from septum_core.model_integrity import ModelRefusal

from septum_api.models.document import Document
from septum_api.routers import chat as chat_module
from septum_api.services.anonymization_map import AnonymizationMap
from septum_api.services.approval_gate import (
    ApprovalChunk,
    ApprovalDecision,
    ApprovalSessionForbiddenError,
)
from septum_api.services.deanonymizer import Deanonymizer
from septum_api.services.llm_router import LLMRouter, LLMRouterError
from septum_api.services.pii_gate import (
    DocumentGoneError,
    DocumentMapConflictError,
    DocumentMapMissingError,
)
from septum_api.services.prompts import PromptCatalog
from septum_api.services.remask_cache import remask_cache

SESSION = "s-1"
DEBUG_ID = "dbg-1"
P1 = "[PERSON_NAME_1]"
P2 = "[PERSON_NAME_2]"
QUERY = "Who is Jane Roe?"
MASKED_QUERY = f"Who is {P1}?"
LONG_QUERY = "Please list every party named in the signed contract."
CHAT_LOGGER = "septum_api.routers.chat"
_ELAPSED = re.compile(r"elapsed_ms=\d+(\.\d+)?")


class _ScriptedGate:
    """Approval gate double with a scripted decision."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.discarded: list[str] = []
        self.decision = ApprovalDecision(approved=False, chunks=[], reason="no")
        self.create_error: Optional[Exception] = None
        self.delay = 0.0
        self.hang = False
        self.wait_task: Optional[asyncio.Task] = None

    async def create(self, **kwargs: Any) -> str:
        if self.create_error is not None:
            raise self.create_error
        self.created.append(kwargs)
        return "handle"

    def discard_session(self, session_id: str, handle: Any) -> None:
        self.discarded.append(session_id)

    async def wait_for_approval(self, session_id: str, timeout: float) -> ApprovalDecision:
        self.wait_task = asyncio.current_task()
        if self.hang:
            await asyncio.Event().wait()
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.decision


@dataclass
class Turn:
    """Everything one chat turn produced."""

    status: int
    raw: str
    events: list[dict[str, Any]]
    logs: list[str]
    prompts: list[str]
    llm_kwargs: list[dict[str, Any]]
    audits: list[tuple[str, dict[str, Any]]]
    error_logs: list[tuple[str, Optional[int]]]
    detail: Any = None


@dataclass
class CharEnv:
    """Characterization harness on top of the shared chat harness."""

    base: ChatEnv
    gate: _ScriptedGate
    caplog: pytest.LogCaptureFixture
    maps: dict[int, AnonymizationMap] = field(default_factory=dict)
    answer: Any = f"The person is {P1}."
    llm_kwargs: list[dict[str, Any]] = field(default_factory=list)
    audits: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    error_logs: list[tuple[str, Optional[int]]] = field(default_factory=list)

    async def doc(
        self,
        filename: str,
        chunks: list[str],
        entities: Optional[list[tuple[str, str]]] = None,
        owner: str = "alice",
    ) -> int:
        doc_id = await self.base.add_document(self.base.users[owner], filename, chunks)
        if entities is not None:
            amap = AnonymizationMap(document_id=doc_id, language="en")
            for original, entity_type in entities:
                amap.add_entity(original, entity_type)
            self.maps[doc_id] = amap
        return doc_id

    async def ask(self, payload: dict[str, Any]) -> Turn:
        self.base.act_as("alice")
        self.base.prompts.clear()
        self.llm_kwargs.clear()
        self.audits.clear()
        self.error_logs.clear()
        self.caplog.clear()
        body = {"session_id": SESSION, **payload}
        resp = await self.base.client.post("/api/chat/ask", json=body)
        logs = [
            _ELAPSED.sub("elapsed_ms=X", r.getMessage())
            for r in self.caplog.records
            if r.name == CHAT_LOGGER
        ]
        if resp.status_code != 200:
            return Turn(resp.status_code, "", [], logs, [], [], [], [], resp.json())
        events: list[dict[str, Any]] = []
        for line in resp.text.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
            elif line.startswith(":"):
                events.append({"keepalive": True})
        return Turn(
            resp.status_code,
            resp.text,
            events,
            logs,
            list(self.base.prompts),
            list(self.llm_kwargs),
            list(self.audits),
            list(self.error_logs),
        )


@pytest.fixture
async def env(
    chat_env: ChatEnv, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> CharEnv:
    caplog.set_level(logging.DEBUG, logger=CHAT_LOGGER)
    gate = _ScriptedGate()
    cenv = CharEnv(base=chat_env, gate=gate, caplog=caplog)
    remask_cache.clear()

    async def _sanitize(message, language, settings, anon_map, db):
        text, counts, total = message, {}, 0
        for original, entity_type in (("Jane Roe", "PERSON_NAME"), ("John Poe", "PERSON_NAME")):
            if original in text:
                text = text.replace(original, anon_map.add_entity(original, entity_type))
                counts[entity_type] = counts.get(entity_type, 0) + 1
                total += 1
        return text, total, counts

    async def _get_map(document_id):
        return cenv.maps.get(document_id)

    async def _complete(self, messages, **kwargs):
        chat_env.prompts.append(messages[-1]["content"])
        cenv.llm_kwargs.append(kwargs)
        if isinstance(cenv.answer, BaseException):
            raise cenv.answer
        if callable(cenv.answer):
            return await cenv.answer(kwargs)
        return cenv.answer

    async def _audit_pii(db, **kwargs):
        cenv.audits.append(("pii_detected", kwargs))

    async def _audit_deanon(db, **kwargs):
        cenv.audits.append(("deanonymization", kwargs))

    async def _log_error(db, request, exc, status_code=None):
        cenv.error_logs.append((type(exc).__name__, status_code))

    monkeypatch.setattr(chat_module, "_sanitize_query", _sanitize)
    monkeypatch.setattr(chat_module, "get_document_map", _get_map)
    monkeypatch.setattr(chat_module, "Deanonymizer", Deanonymizer)
    monkeypatch.setattr(chat_module, "get_approval_gate", lambda: gate)
    monkeypatch.setattr(chat_module, "new_debug_id", lambda: DEBUG_ID)
    monkeypatch.setattr(chat_module, "log_pii_detected", _audit_pii)
    monkeypatch.setattr(chat_module, "log_deanonymization", _audit_deanon)
    monkeypatch.setattr(chat_module, "log_backend_error", _log_error)
    monkeypatch.setattr(LLMRouter, "complete", _complete)
    await chat_env.set_require_approval(False)
    return cenv


def _meta(**overrides: Any) -> dict[str, Any]:
    meta = {
        "type": "meta",
        "session_id": SESSION,
        "debug_id": DEBUG_ID,
        "document_id": None,
        "language": "en",
        "require_approval": False,
        "retrieved_chunk_count": 0,
        "active_regulations": [],
        "rag_mode": "manual",
        "matched_document_ids": [],
        "matched_document_names": [],
        "matched_documents": [],
    }
    meta.update(overrides)
    return meta


def _phase(name: str) -> str:
    return f"chat phase session_id={SESSION} phase={name} elapsed_ms=X"


def _end(fallback: bool = False) -> dict[str, Any]:
    return {"type": "end", "used_ollama_fallback": fallback}


def _prompt(
    query: str,
    chunks: list[str],
    language: str = "en",
    query_has_placeholder: bool = False,
    output_mode: str = "chat",
) -> str:
    return chat_module._assemble_user_prompt(
        sanitized_query=query,
        context_chunks=chunks,
        regulation_names=[],
        language=language,
        output_mode=output_mode,
        column_descriptions=[],
        query_has_placeholder=query_has_placeholder,
    )


def _deanon_log(answer: list[str], residual: list[str], map_size: int, missing: list[str]) -> str:
    return (
        f"chat deanon session_id={SESSION} strategy=simple map_size={map_size} "
        f"answer_placeholders={answer} residual={residual} missing_in_map={missing}"
    )


class TestManualTurn:
    async def test_single_document_answer_is_restored_and_streamed(self, env: CharEnv) -> None:
        doc = await env.doc("contract.pdf", [f"Signed by {P1}."], [("Jane Roe", "PERSON_NAME")])

        turn = await env.ask({"message": QUERY, "document_ids": [doc]})

        assert turn.status == 200
        assert turn.events == [
            _meta(
                document_id=doc,
                retrieved_chunk_count=1,
                matched_documents=[{"id": doc, "name": "contract.pdf", "chunk_count": 1}],
            ),
            {"type": "answer_chunk", "text": "The person is Jane Roe."},
            _end(),
        ]
        assert turn.prompts == [_prompt(MASKED_QUERY, [f"Signed by {P1}."])]
        assert [list(k) for k in turn.llm_kwargs] == [["on_cloud_failure"]]
        assert turn.audits == [
            (
                "pii_detected",
                {
                    "document_id": doc,
                    "regulation_ids": [],
                    "entity_type_counts": {"PERSON_NAME": 1},
                    "total_count": 1,
                    "session_id": SESSION,
                    "extra": {"source": "chat_query"},
                },
            ),
            (
                "deanonymization",
                {"document_id": doc, "entity_count": 1, "strategy": "simple", "session_id": SESSION},
            ),
        ]
        assert turn.logs == [
            f"chat document_id={doc} anon_map_entries=1 deanon_enabled=True",
            _phase("sanitize_query"),
            _phase("retrieve_chunks"),
            _phase("resanitize_outdated_chunks"),
            f"chat unified-map session_id={SESSION} docs=[{doc}] unified_entries=1 "
            f"query_entries=1 query_remap_size=0 placeholders=['{P1}']",
            _deanon_log([P1], [], 1, []),
            _phase("llm_and_deanonymize"),
        ]
        assert "Jane Roe" not in "".join(turn.prompts)

    async def test_two_documents_are_unified_and_labelled(self, env: CharEnv) -> None:
        first = await env.doc("a.pdf", [f"Signed by {P1}."], [("Jane Roe", "PERSON_NAME")])
        second = await env.doc("b.pdf", [f"Paid by {P1}."], [("John Poe", "PERSON_NAME")])
        env.answer = f"{P1} and {P2}"

        turn = await env.ask({"message": QUERY, "document_ids": [first, second]})

        chunks = [
            PromptCatalog.document_context_header(1) + f"Signed by {P1}.",
            PromptCatalog.document_context_header(2) + f"Paid by {P2}.",
        ]
        assert turn.events == [
            _meta(
                document_id=first,
                retrieved_chunk_count=2,
                matched_documents=[
                    {"id": first, "name": "a.pdf", "chunk_count": 1},
                    {"id": second, "name": "b.pdf", "chunk_count": 1},
                ],
            ),
            {"type": "answer_chunk", "text": "Jane Roe and John Poe"},
            _end(),
        ]
        assert turn.prompts == [_prompt(MASKED_QUERY, chunks)]
        assert turn.logs[4] == (
            f"chat unified-map session_id={SESSION} docs=[{first}, {second}] unified_entries=2 "
            f"query_entries=1 query_remap_size=0 placeholders=['{P1}', '{P2}']"
        )
        assert turn.audits[1] == (
            "deanonymization",
            {"document_id": first, "entity_count": 2, "strategy": "simple", "session_id": SESSION},
        )

    async def test_long_answer_is_streamed_in_256_char_pieces(self, env: CharEnv) -> None:
        doc = await env.doc("a.pdf", ["alpha"], [("Jane Roe", "PERSON_NAME")])
        env.answer = ("word " * 120) + P1

        turn = await env.ask({"message": QUERY, "document_ids": [doc]})

        pieces = [e["text"] for e in turn.events if e.get("type") == "answer_chunk"]
        assert len(pieces) > 1
        assert all(len(p) <= 256 for p in pieces)
        assert "".join(pieces) == ("word " * 120) + "Jane Roe"
        assert turn.events[-1] == _end()

    async def test_document_without_map_warns_and_appends_note(self, env: CharEnv) -> None:
        doc = await env.doc("a.pdf", [f"Signed by {P1}."])
        env.answer = f"It is {P1}."

        turn = await env.ask({"message": "who signed?", "document_ids": [doc]})

        assert turn.events[1] == {
            "type": "answer_chunk",
            "text": (
                f"It is {P1}.\n\n(Names and other values could not be loaded. "
                "Check the encryption key in Settings and re-upload the document.)"
            ),
        }
        assert turn.logs[:2] == [
            f"chat document_id={doc} anon_map_entries=0 deanon_enabled=True",
            f"Deanonymization will have no effect: no map for document_id={doc}. "
            "Encryption key may have changed — re-upload the document so the map can be loaded.",
        ]
        assert turn.audits == []

    async def test_request_can_disable_deanonymization(self, env: CharEnv) -> None:
        doc = await env.doc("a.pdf", [f"Signed by {P1}."], [("Jane Roe", "PERSON_NAME")])

        turn = await env.ask({"message": QUERY, "document_ids": [doc], "deanon_enabled": False})

        assert turn.events[1] == {"type": "answer_chunk", "text": f"The person is {P1}."}
        assert [a[0] for a in turn.audits] == ["pii_detected"]

    async def test_output_mode_is_normalised(self, env: CharEnv) -> None:
        doc = await env.doc("a.pdf", ["alpha"], [("Jane Roe", "PERSON_NAME")])

        turn = await env.ask({"message": LONG_QUERY, "document_ids": [doc], "output_mode": " TABLE "})

        assert turn.prompts == [_prompt(LONG_QUERY, ["alpha"], output_mode="table")]

    async def test_cloud_failure_sets_fallback_flag(self, env: CharEnv) -> None:
        doc = await env.doc("a.pdf", ["alpha"], [("Jane Roe", "PERSON_NAME")])
        messages: list[str] = []

        async def _record(db, request, message, level=None, extra=None):
            messages.append(message)

        async def _answer(kwargs):
            await kwargs["on_cloud_failure"]("cloud down", {})
            return "local"

        env.answer = _answer
        chat_module_log = chat_module.log_backend_message
        try:
            chat_module.log_backend_message = _record
            turn = await env.ask({"message": "hi", "document_ids": [doc]})
        finally:
            chat_module.log_backend_message = chat_module_log

        assert turn.events[-2:] == [{"type": "answer_chunk", "text": "local"}, _end(True)]
        assert messages == ["cloud down"]


class TestPlaceholderAnswers:
    async def test_typed_placeholder_forces_the_answer(self, env: CharEnv) -> None:
        doc = await env.doc("a.pdf", [f"Signed by {P1}."], [("Jane Roe", "PERSON_NAME")])
        env.answer = "a long unrelated answer"

        turn = await env.ask({"message": f"Who is {P1}? and {P1}", "document_ids": [doc]})

        assert turn.events[1] == {"type": "answer_chunk", "text": "Jane Roe"}
        assert turn.prompts == [
            _prompt(f"Who is {P1}? and {P1}", [f"Signed by {P1}."], query_has_placeholder=True)
        ]

    async def test_typed_unknown_placeholder_is_not_restored(self, env: CharEnv) -> None:
        doc = await env.doc("a.pdf", ["alpha"], [("Jane Roe", "PERSON_NAME")])

        turn = await env.ask({"message": "Who is [PERSON_NAME_7]?", "document_ids": [doc]})

        assert turn.events[1] == {"type": "answer_chunk", "text": "[PERSON_NAME_7]"}
        assert _deanon_log(["[PERSON_NAME_7]"], ["[PERSON_NAME_7]"], 1, ["[PERSON_NAME_7]"]) in turn.logs

    async def test_placeholder_outside_the_prompt_stays_literal(self, env: CharEnv) -> None:
        doc = await env.doc(
            "a.pdf", ["alpha"], [("Jane Roe", "PERSON_NAME"), ("John Poe", "PERSON_NAME")]
        )
        env.answer = f"{P1} and {P2}"

        turn = await env.ask({"message": QUERY, "document_ids": [doc]})

        assert turn.events[1] == {"type": "answer_chunk", "text": f"Jane Roe and {P2}"}
        assert _deanon_log([P1, P2], [P2], 2, []) in turn.logs


class TestAutoRag:
    async def test_auto_turn_retrieves_from_matching_documents(self, env: CharEnv) -> None:
        doc = await env.doc("own.pdf", [f"Signed by {P1}."], [("Jane Roe", "PERSON_NAME")])

        turn = await env.ask({"message": "who signed?"})

        assert turn.events == [
            _meta(
                document_id=doc,
                retrieved_chunk_count=1,
                rag_mode="auto",
                matched_document_ids=[doc],
                matched_document_names=["own.pdf"],
                matched_documents=[{"id": doc, "name": "own.pdf", "chunk_count": 1}],
            ),
            {"type": "answer_chunk", "text": "The person is Jane Roe."},
            _end(),
        ]
        assert turn.logs == [
            "chat document_id=None anon_map_entries=0 deanon_enabled=True",
            _phase("sanitize_query"),
            _phase("auto_rag_classify"),
            _phase("retrieve_chunks_auto"),
            _phase("resanitize_outdated_chunks"),
            f"chat unified-map session_id={SESSION} docs=[{doc}] unified_entries=1 "
            f"query_entries=0 query_remap_size=0 placeholders=['{P1}']",
            _deanon_log([P1], [], 1, []),
            _phase("llm_and_deanonymize"),
        ]
        assert turn.audits == [
            (
                "deanonymization",
                {"document_id": doc, "entity_count": 1, "strategy": "simple", "session_id": SESSION},
            )
        ]

    async def test_no_documents_is_a_pure_chat_turn(self, env: CharEnv) -> None:
        turn = await env.ask({"message": QUERY})

        assert turn.events == [
            _meta(language="auto", rag_mode="none"),
            {"type": "answer_chunk", "text": "The person is Jane Roe."},
            _end(),
        ]
        assert turn.prompts == [PromptCatalog.pure_chat_prompt(MASKED_QUERY)]
        assert turn.audits[0] == (
            "pii_detected",
            {
                "document_id": 0,
                "regulation_ids": [],
                "entity_type_counts": {"PERSON_NAME": 1},
                "total_count": 1,
                "session_id": SESSION,
                "extra": {"source": "chat_query"},
            },
        )
        assert turn.logs == [
            "chat document_id=None anon_map_entries=0 deanon_enabled=True",
            _phase("sanitize_query"),
            _phase("auto_rag_classify"),
            _deanon_log([P1], [], 1, []),
            _phase("llm_and_deanonymize"),
        ]

    async def test_empty_retrieval_falls_back_to_pure_chat(
        self, env: CharEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await env.doc("own.pdf", ["alpha"], [("Jane Roe", "PERSON_NAME")])

        async def _nothing(db, query, top_k, documents, relevance_threshold):
            return [], []

        monkeypatch.setattr(chat_module, "_retrieve_chunks_all_documents", _nothing)

        turn = await env.ask({"message": "hello"})

        assert turn.events[0] == _meta(language="auto", rag_mode="none")
        assert turn.prompts == [PromptCatalog.pure_chat_prompt("hello")]
        assert _phase("retrieve_chunks_auto") in turn.logs

    async def test_chat_intent_skips_retrieval(
        self, env: CharEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await env.doc("own.pdf", ["alpha"], [("Jane Roe", "PERSON_NAME")])
        seen: list[dict[str, Any]] = []

        async def _chat(**kwargs):
            seen.append(kwargs)
            return "chat"

        monkeypatch.setattr(chat_module, "_classify_query_intent", _chat)

        turn = await env.ask({"message": "hello"})

        assert turn.events[0] == _meta(language="auto", rag_mode="none")
        assert seen == [
            {
                "query": "hello",
                "document_names": ["own.pdf"],
                "ollama_base_url": seen[0]["ollama_base_url"],
                "ollama_model": seen[0]["ollama_model"],
            }
        ]
        assert _phase("retrieve_chunks_auto") not in turn.logs

    async def test_scoped_doc_ids_restrict_candidates(self, env: CharEnv) -> None:
        keep = await env.doc("keep.pdf", ["kept chunk"], [("Jane Roe", "PERSON_NAME")])
        await env.doc("drop.pdf", ["dropped chunk"], [("John Poe", "PERSON_NAME")])

        turn = await env.ask({"message": "anything", "scoped_doc_ids": [keep]})

        assert turn.events[0]["matched_document_ids"] == [keep]
        assert "dropped chunk" not in turn.prompts[0]
        assert (
            f"chat entity-routing session_id={SESSION} reason=disambiguation_choice "
            "candidates=2 narrowed=1"
        ) in turn.logs

    @pytest.mark.parametrize(
        ("score", "reason", "narrows"),
        [
            (1.0, "strong_entity_match", True),
            (None, "medium_entity_match", True),
            (0.0, "weak_entity_match_only", False),
        ],
    )
    async def test_entity_routing(
        self,
        env: CharEnv,
        monkeypatch: pytest.MonkeyPatch,
        score: Optional[float],
        reason: str,
        narrows: bool,
    ) -> None:
        keep = await env.doc("keep.pdf", ["kept chunk"], [("Jane Roe", "PERSON_NAME")])
        other = await env.doc("other.pdf", ["other chunk"], [("John Poe", "PERSON_NAME")])
        value = chat_module.RELATIONSHIP_THRESHOLD_MEDIUM if score is None else score
        calls: list[tuple[Any, ...]] = []

        async def _scores(db, entities, owner_id=None):
            calls.append((entities, owner_id))
            return {keep: value}

        async def _never(**kwargs):
            raise AssertionError("classifier must be skipped")

        monkeypatch.setattr(chat_module, "find_documents_for_query_entities", _scores)
        monkeypatch.setattr(chat_module, "_classify_query_intent", _never)

        turn = await env.ask({"message": QUERY})

        assert calls == [([("Jane Roe", "PERSON_NAME")], env.base.users["alice"].id)]
        routing = (
            f"chat entity-routing session_id={SESSION} reason={reason} candidates=2 narrowed=1"
        )
        assert (routing in turn.logs) is narrows
        assert (
            f"chat intent-classifier session_id={SESSION} skipped reason={reason}" in turn.logs
        )
        expected_ids = [keep] if narrows else [keep, other]
        assert turn.events[0]["matched_document_ids"] == expected_ids

    async def test_no_entity_score_consults_the_classifier(
        self, env: CharEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await env.doc("keep.pdf", ["kept chunk"], [("Jane Roe", "PERSON_NAME")])
        classified: list[str] = []

        async def _scores(db, entities, owner_id=None):
            return {}

        async def _classify(**kwargs):
            classified.append(kwargs["query"])
            return "search"

        monkeypatch.setattr(chat_module, "find_documents_for_query_entities", _scores)
        monkeypatch.setattr(chat_module, "_classify_query_intent", _classify)

        turn = await env.ask({"message": QUERY})

        assert classified == [MASKED_QUERY]
        assert turn.events[0]["rag_mode"] == "auto"


class TestApproval:
    async def test_rejected_turn_sends_nothing(self, env: CharEnv) -> None:
        doc = await env.doc("a.pdf", [f"Signed by {P1}."], [("Jane Roe", "PERSON_NAME")])
        env.gate.decision = ApprovalDecision(
            approved=False, chunks=[], reason="nope", timed_out=True
        )

        turn = await env.ask({"message": QUERY, "document_ids": [doc], "require_approval": True})

        token = turn.events[1]["numbering_token"]
        assert isinstance(token, str) and token
        assert turn.events == [
            _meta(
                document_id=doc,
                require_approval=True,
                retrieved_chunk_count=1,
                matched_documents=[{"id": doc, "name": "a.pdf", "chunk_count": 1}],
            ),
            {
                "type": "approval_required",
                "session_id": SESSION,
                "numbering_token": token,
                "masked_prompt": MASKED_QUERY,
                "assembled_prompt": _prompt(MASKED_QUERY, [f"Signed by {P1}."]),
                "chunks": [
                    {
                        "id": turn.events[1]["chunks"][0]["id"],
                        "document_id": doc,
                        "text": f"Signed by {P1}.",
                        "source_page": None,
                        "section_title": None,
                    }
                ],
            },
            {"type": "approval_rejected", "session_id": SESSION, "reason": "nope", "timed_out": True},
            {"type": "end"},
        ]
        assert turn.prompts == []
        created = env.gate.created[0]
        assert created["session_id"] == SESSION
        assert created["masked_prompt"] == MASKED_QUERY
        assert created["masked_chunks"] == [f"Signed by {P1}."]
        assert created["entity_count"] == 1
        assert created["owner_user_id"] == env.base.users["alice"].id
        assert created["anon_map"].entity_map == {"Jane Roe": P1}
        assert created["assembly_context"] == {
            "sanitized_query": MASKED_QUERY,
            "regulation_names": [],
            "language": "en",
            "output_mode": "chat",
            "column_descriptions": [],
            "query_has_placeholder": False,
        }
        assert env.gate.discarded == [SESSION]
        assert _phase("approval_wait") in turn.logs
        assert [a[0] for a in turn.audits] == ["pii_detected"]

    async def test_approved_turn_gates_edited_chunks(self, env: CharEnv) -> None:
        doc = await env.doc("a.pdf", [f"Signed by {P1}."], [("Jane Roe", "PERSON_NAME")])
        env.gate.decision = ApprovalDecision(
            approved=True,
            chunks=[
                ApprovalChunk(id=1, document_id=doc, text=f"Signed by {P1}."),
                ApprovalChunk(id=None, document_id=None, text="added by user"),
            ],
        )

        turn = await env.ask({"message": QUERY, "document_ids": [doc], "require_approval": True})

        assert [e.get("type") for e in turn.events] == [
            "meta",
            "approval_required",
            "answer_chunk",
            "end",
        ]
        assert turn.events[-2:] == [
            {"type": "answer_chunk", "text": "The person is Jane Roe."},
            _end(),
        ]
        assert turn.prompts == [_prompt(MASKED_QUERY, [f"Signed by {P1}.", "added by user"])]
        assert env.gate.discarded == [SESSION]

    async def test_slow_decision_emits_keepalives(
        self, env: CharEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        doc = await env.doc("a.pdf", ["alpha"], [("Jane Roe", "PERSON_NAME")])
        env.gate.decision = ApprovalDecision(approved=True, chunks=[])
        env.gate.delay = 0.2
        real_wait = asyncio.wait

        async def _fast_wait(tasks, timeout=None, **kwargs):
            if timeout == 15.0:
                timeout = 0.01
            return await real_wait(tasks, timeout=timeout, **kwargs)

        monkeypatch.setattr(asyncio, "wait", _fast_wait)

        turn = await env.ask({"message": LONG_QUERY, "document_ids": [doc], "require_approval": True})

        kinds = [e.get("type", "keepalive") for e in turn.events]
        first_answer = kinds.index("answer_chunk")
        assert kinds[:2] == ["meta", "approval_required"]
        assert "keepalive" in kinds[2:first_answer]
        assert set(kinds[2:first_answer]) == {"keepalive"}
        assert ": keepalive\n\n" in turn.raw
        assert turn.prompts == [_prompt(LONG_QUERY, [])]

    async def test_disconnect_during_wait_discards_session_and_cancels_wait(
        self, env: CharEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from starlette.requests import Request

        doc = await env.doc("a.pdf", ["alpha"], [("Jane Roe", "PERSON_NAME")])
        env.gate.hang = True
        real_wait = asyncio.wait

        async def _fast_wait(tasks, timeout=None, **kwargs):
            if timeout == 15.0:
                timeout = 0.01
            return await real_wait(tasks, timeout=timeout, **kwargs)

        monkeypatch.setattr(asyncio, "wait", _fast_wait)
        http_request = Request({"type": "http", "method": "POST", "path": "/api/chat/ask", "headers": []})

        async with env.base.session_maker() as db:
            response = await chat_module.chat_ask(
                chat_module.ChatRequest(
                    message=LONG_QUERY,
                    document_ids=[doc],
                    session_id=SESSION,
                    require_approval=True,
                ),
                http_request,
                db=db,
                current_user=env.base.users["alice"],
            )
            stream = response.body_iterator
            received: list[bytes] = []
            async for chunk in stream:
                received.append(chunk)
                if chunk == b": keepalive\n\n":
                    break
            assert env.gate.discarded == []

            await stream.aclose()

        assert b'"type": "approval_required"' in received[1]
        assert env.gate.discarded == [SESSION]
        wait_task = env.gate.wait_task
        assert wait_task is not None
        await real_wait({wait_task}, timeout=1.0)
        assert wait_task.cancelled()
        assert env.base.prompts == []

    async def test_forbidden_session_reports_not_found(self, env: CharEnv) -> None:
        doc = await env.doc("a.pdf", ["alpha"], [("Jane Roe", "PERSON_NAME")])
        env.gate.create_error = ApprovalSessionForbiddenError("x")

        turn = await env.ask({"message": "hi", "document_ids": [doc], "require_approval": True})

        assert turn.events[1:] == [{"type": "error", "message": "Approval session not found."}]
        assert env.gate.discarded == []
        assert turn.prompts == []


class TestRetry:
    async def _first_token(self, env: CharEnv, doc: int) -> str:
        turn = await env.ask({"message": QUERY, "document_ids": [doc], "require_approval": True})
        return turn.events[1]["numbering_token"]

    async def test_valid_retry_sends_the_gated_client_chunks(self, env: CharEnv) -> None:
        doc = await env.doc("a.pdf", [f"Signed by {P1}."], [("Jane Roe", "PERSON_NAME")])
        token = await self._first_token(env, doc)

        turn = await env.ask(
            {
                "message": QUERY,
                "document_ids": [doc],
                "numbering_token": token,
                "pre_approved_chunks": [{"text": f"Signed by {P1}.", "document_id": doc}],
            }
        )

        assert turn.events == [
            _meta(document_id=doc),
            {"type": "answer_chunk", "text": "The person is Jane Roe."},
            _end(),
        ]
        assert turn.prompts == [_prompt(MASKED_QUERY, [f"Signed by {P1}."])]
        assert turn.logs == [
            f"chat document_id={doc} anon_map_entries=1 deanon_enabled=True",
            _phase("sanitize_query"),
            _deanon_log([P1], [], 1, []),
            _phase("llm_and_deanonymize"),
        ]

    async def test_auto_mode_retry_uses_the_chunk_documents(self, env: CharEnv) -> None:
        doc = await env.doc("a.pdf", [f"Signed by {P1}."], [("Jane Roe", "PERSON_NAME")])
        first = await env.ask({"message": QUERY, "require_approval": True})
        token = first.events[1]["numbering_token"]

        turn = await env.ask(
            {
                "message": QUERY,
                "numbering_token": token,
                "pre_approved_chunks": [{"text": f"Signed by {P1}.", "document_id": doc}],
            }
        )

        assert turn.events[0] == _meta(document_id=doc, rag_mode="auto")
        assert turn.events[1:] == [
            {"type": "answer_chunk", "text": "The person is Jane Roe."},
            _end(),
        ]

    async def test_changed_numbering_refuses_the_retry(self, env: CharEnv) -> None:
        doc = await env.doc("a.pdf", [f"Signed by {P1}."], [("Jane Roe", "PERSON_NAME")])

        turn = await env.ask(
            {
                "message": QUERY,
                "document_ids": [doc],
                "numbering_token": "v1.bogus",
                "pre_approved_chunks": [{"text": "x", "document_id": doc}],
            }
        )

        assert turn.events == [
            _meta(document_id=doc),
            {
                "type": "error",
                "code": chat_module.RETRY_NUMBERING_CHANGED,
                "message": "The documents changed since this answer was prepared; please ask again.",
            },
        ]
        assert (
            f"chat retry refused session_id={SESSION} reason=retry_numbering_changed docs=[{doc}]"
            in turn.logs
        )
        assert turn.prompts == []

    async def test_documentless_retry_without_token_is_sent(self, env: CharEnv) -> None:
        turn = await env.ask({"message": LONG_QUERY, "pre_approved_chunks": [{"text": "client text"}]})

        assert turn.events == [
            _meta(rag_mode="auto"),
            {
                "type": "answer_chunk",
                "text": (
                    f"The person is {P1}.\n\n(Names and other values could not be loaded. "
                    "Check the encryption key in Settings and re-upload the document.)"
                ),
            },
            _end(),
        ]
        assert turn.prompts == [_prompt(LONG_QUERY, ["client text"])]

    async def test_documentless_retry_with_bad_token_is_refused(self, env: CharEnv) -> None:
        turn = await env.ask(
            {"message": "hi", "numbering_token": "v1.bogus", "pre_approved_chunks": [{"text": "t"}]}
        )

        assert turn.events[1]["code"] == chat_module.RETRY_NUMBERING_CHANGED
        assert f"chat retry refused session_id={SESSION} reason=retry_numbering_changed docs=[]" in turn.logs


class TestResanitize:
    async def test_grown_seed_map_reseeds_the_query(
        self, env: CharEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        doc = await env.doc("a.pdf", ["Signed by John Poe."], [("Jane Roe", "PERSON_NAME")])
        async with env.base.session_maker() as session:
            row = await session.get(Document, doc)
            row.ingestion_status = "processing"
            await session.commit()

        async def _remask(db, settings, document, texts, sanitizer):
            env.maps[document.id].add_entity("John Poe", "PERSON_NAME")
            return [t.replace("John Poe", P2) for t in texts], True

        monkeypatch.setattr(chat_module, "remask_document_texts", _remask)
        env.answer = f"{P1} / {P2}"

        turn = await env.ask({"message": QUERY, "document_ids": [doc]})

        assert turn.events[1:] == [
            {"type": "answer_chunk", "text": "Jane Roe / John Poe"},
            _end(),
        ]
        assert turn.prompts == [_prompt(MASKED_QUERY, [f"Signed by {P2}."])]
        assert (
            f"chat unified-map session_id={SESSION} docs=[{doc}] unified_entries=2 "
            f"query_entries=2 query_remap_size=0 placeholders=['{P1}', '{P2}']"
        ) in turn.logs

    async def test_missing_seed_map_after_remask_is_document_changed(
        self, env: CharEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        doc = await env.doc("a.pdf", ["alpha"], [("Jane Roe", "PERSON_NAME")])
        async with env.base.session_maker() as session:
            row = await session.get(Document, doc)
            row.ingestion_status = "processing"
            await session.commit()

        async def _remask(db, settings, document, texts, sanitizer):
            env.maps.pop(document.id)
            return texts, True

        monkeypatch.setattr(chat_module, "remask_document_texts", _remask)

        turn = await env.ask({"message": QUERY, "document_ids": [doc]})

        assert turn.events[1:] == [
            {
                "type": "error",
                "code": chat_module.DOCUMENT_CHANGED,
                "message": "A document changed while the question was being answered; please ask again.",
            }
        ]


class TestErrors:
    async def test_missing_message_is_422(self, env: CharEnv) -> None:
        turn = await env.ask({"document_ids": []})

        assert turn.status == 422
        assert turn.detail == {"detail": "Either 'message' or 'query' must be provided."}

    async def test_legacy_query_field_is_accepted(self, env: CharEnv) -> None:
        doc = await env.doc("a.pdf", ["alpha"], [("Jane Roe", "PERSON_NAME")])

        turn = await env.ask({"query": QUERY, "document_id": doc})

        assert turn.events[0]["document_id"] == doc
        assert turn.events[-1] == _end()

    @pytest.mark.parametrize(
        "error", [DocumentMapMissingError("gone"), DocumentGoneError()], ids=["missing", "gone"]
    )
    async def test_document_changed(self, env: CharEnv, error: Exception) -> None:
        doc = await env.doc("a.pdf", ["alpha"], [("Jane Roe", "PERSON_NAME")])
        env.answer = error

        turn = await env.ask({"message": "hi", "document_ids": [doc]})

        assert turn.events[1:] == [
            {
                "type": "error",
                "code": "document_changed",
                "message": "A document changed while the question was being answered; please ask again.",
            }
        ]
        assert turn.logs[-1] == (
            f"chat turn refused session_id={SESSION} reason=document_changed "
            f"error={type(error).__name__}"
        )
        assert turn.error_logs == []

    async def test_map_conflict(self, env: CharEnv) -> None:
        doc = await env.doc("a.pdf", ["alpha"], [("Jane Roe", "PERSON_NAME")])
        env.answer = DocumentMapConflictError()

        turn = await env.ask({"message": "hi", "document_ids": [doc]})

        assert turn.events[1:] == [
            {
                "type": "error",
                "code": "map_conflict",
                "message": "The document is being updated; please try again.",
            }
        ]
        assert turn.logs[-1] == f"chat turn refused session_id={SESSION} reason=map_conflict"

    @pytest.mark.parametrize(
        ("error", "code", "message"),
        [
            (LLMRouterError("upstream detail"), "llm_unavailable", chat_module.LLM_UNAVAILABLE_MESSAGE),
            (LLMRouterError("authored text", code="some_code"), "some_code", "authored text"),
        ],
        ids=["uncoded", "coded"],
    )
    async def test_llm_error(self, env: CharEnv, error: Exception, code: str, message: str) -> None:
        doc = await env.doc("a.pdf", ["alpha"], [("Jane Roe", "PERSON_NAME")])
        env.answer = error

        turn = await env.ask({"message": "hi", "document_ids": [doc]})

        assert turn.events[1:] == [{"type": "error", "code": code, "message": message}]
        assert turn.error_logs == [("LLMRouterError", 400)]
        assert turn.logs[-1] == (
            f"chat LLM call failed session_id={SESSION} code={code} error=LLMRouterError status=400"
        )
        assert turn.logs[-2] == _phase("llm_and_deanonymize")

    async def test_unexpected_error(self, env: CharEnv) -> None:
        doc = await env.doc("a.pdf", ["alpha"], [("Jane Roe", "PERSON_NAME")])
        env.answer = RuntimeError("secret Jane Roe text")

        turn = await env.ask({"message": "hi", "document_ids": [doc]})

        assert turn.events[1:] == [
            {
                "type": "error",
                "message": "An unexpected error occurred while processing the chat request.",
            }
        ]
        assert turn.error_logs == [("RuntimeError", 500)]
        assert turn.logs[-1] == "Unhandled exception in chat event stream: RuntimeError"
        assert all("Jane Roe" not in line for line in turn.logs)

    async def test_model_refusal(self, env: CharEnv) -> None:
        doc = await env.doc("a.pdf", ["alpha"], [("Jane Roe", "PERSON_NAME")])
        env.answer = ModelRefusal("refused")

        turn = await env.ask({"message": "hi", "document_ids": [doc]})

        assert turn.events[1:] == [
            {"type": "error", "message": chat_module.MODEL_REFUSED_DETAIL, "code": "model_refused"}
        ]
        assert turn.error_logs == [("ModelRefusal", 503)]
        assert turn.logs[-1] == "Chat refused, a model was refused (code: model_refused)"

    async def test_error_before_meta_has_no_meta(
        self, env: CharEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _broken(db):
            raise RuntimeError("db down")

        monkeypatch.setattr(chat_module, "_active_regulations", _broken)

        turn = await env.ask({"message": "hi"})

        assert turn.events == [
            {
                "type": "error",
                "message": "An unexpected error occurred while processing the chat request.",
            }
        ]
        assert turn.error_logs == [("RuntimeError", 500)]


class TestCancellation:
    async def test_cancelled_stream_logs_and_reraises(
        self, env: CharEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from starlette.requests import Request

        doc = await env.doc("a.pdf", ["alpha"], [("Jane Roe", "PERSON_NAME")])
        env.answer = asyncio.CancelledError()
        http_request = Request({"type": "http", "method": "POST", "path": "/api/chat/ask", "headers": []})

        async with env.base.session_maker() as db:
            response = await chat_module.chat_ask(
                chat_module.ChatRequest(message="hi", document_ids=[doc], session_id=SESSION),
                http_request,
                db=db,
                current_user=env.base.users["alice"],
            )
            received: list[bytes] = []
            with pytest.raises(asyncio.CancelledError):
                async for chunk in response.body_iterator:
                    received.append(chunk)

        assert len(received) == 1 and b'"type": "meta"' in received[0]
        messages = [r.getMessage() for r in env.caplog.records if r.name == CHAT_LOGGER]
        assert messages[-1] == "Client disconnected from chat SSE stream"


class TestRunLlmDirect:
    """Branches of the LLM step the stream handler never reaches on its own."""

    async def _run(self, env: CharEnv, answer: str, **overrides: Any) -> str:
        from septum_api.database import build_default_app_settings
        from septum_api.services import chat_debug_store

        env.answer = answer
        amap = AnonymizationMap(document_id=0, language="en")
        amap.add_entity("Jane Roe", "PERSON_NAME")
        amap.add_entity("John Poe", "PERSON_NAME")
        fields: dict[str, Any] = {
            "sanitized_query": "who?",
            "context_chunks": [f"{P1} met {P2}."],
            "regulation_names": [],
            "language": "en",
            "query_has_placeholder": True,
            "debug_id": "dbg-direct",
            "debug_owner_user_id": 7,
        }
        fields.update(overrides)
        async with env.base.session_maker() as db:
            result = await chat_module._run_llm_and_deanonymize(
                db,
                build_default_app_settings(),
                amap,
                chat_module._LlmTurnInput(**fields),
            )
        record = chat_debug_store.get_chat_debug_record(
            "dbg-direct", requester_user_id=7, requester_is_admin=False
        )
        assert record is not None and "Jane Roe" not in record.masked_answer
        return result

    async def test_refusal_is_replaced_by_the_context_placeholders(self, env: CharEnv) -> None:
        result = await self._run(env, "I cannot find that information.")

        assert result == "- Jane Roe\n- John Poe"
        assert env.llm_kwargs == [{"on_cloud_failure": None}]

    async def test_five_bullets_are_replaced_by_the_context_placeholders(
        self, env: CharEnv
    ) -> None:
        result = await self._run(env, "\n".join(f"- item {i}" for i in range(5)))

        assert result == "- Jane Roe\n- John Poe"

    async def test_short_answer_without_placeholders_is_kept(self, env: CharEnv) -> None:
        result = await self._run(env, "- one\n- two")

        assert result == "- one\n- two"

    async def test_pure_chat_restores_only_query_placeholders(self, env: CharEnv) -> None:
        result = await self._run(
            env,
            f"{P1} and {P2}",
            rag_mode="none",
            sanitized_query=f"who is {P1}?",
            query_has_placeholder=False,
        )

        assert result == f"Jane Roe and {P2}"
        assert env.base.prompts == [PromptCatalog.pure_chat_prompt(f"who is {P1}?")]
