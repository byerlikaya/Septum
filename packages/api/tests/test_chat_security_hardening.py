from __future__ import annotations

"""Regression tests for chat-router hardening.

Covers document ownership on ``/api/chat/ask``, auto-RAG owner scoping,
filename-free context headers, the server-side approval floor, and the
masked-only, owner-scoped chat debug store. Every LLM, retrieval and
sanitizer call is mocked.
"""

import json
from typing import Any, Optional

import pytest
from chat_harness import PLACEHOLDER, RAW_VALUE, ChatEnv, answer_text
from sqlalchemy import select

from septum_api import request_limits
from septum_api.models.document import Chunk
from septum_api.routers import chat as chat_module
from septum_api.services import chat_debug_store
from septum_api.services.approval_gate import ApprovalGate
from septum_api.services.llm_router import LLMRouter, LLMRouterError


def _meta(events: list[dict[str, Any]]) -> dict[str, Any]:
    return next(e for e in events if e["type"] == "meta")


class TestChatDocumentOwnership:
    async def test_foreign_document_matches_missing_document_404(self, chat_env: ChatEnv) -> None:
        await chat_env.set_require_approval(False)
        alice_doc = await chat_env.add_document(chat_env.users["alice"], "a.pdf", ["alpha"])

        chat_env.act_as("bob")
        foreign = await chat_env.ask({"message": "who?", "document_ids": [alice_doc]})
        missing = await chat_env.ask({"message": "who?", "document_ids": [alice_doc + 999]})

        assert foreign[0] == missing[0] == 404
        assert foreign[2] == missing[2]
        assert chat_env.prompts == []

    async def test_legacy_single_document_id_is_scoped(self, chat_env: ChatEnv) -> None:
        await chat_env.set_require_approval(False)
        alice_doc = await chat_env.add_document(chat_env.users["alice"], "a.pdf", ["alpha"])

        chat_env.act_as("bob")
        status_code, _, _ = await chat_env.ask({"message": "who?", "document_id": alice_doc})

        assert status_code == 404

    async def test_owner_can_ask_own_document(self, chat_env: ChatEnv) -> None:
        await chat_env.set_require_approval(False)
        alice_doc = await chat_env.add_document(chat_env.users["alice"], "a.pdf", ["alpha"])

        chat_env.act_as("alice")
        status_code, events, _ = await chat_env.ask(
            {"message": "who?", "document_ids": [alice_doc]}
        )

        assert status_code == 200
        assert answer_text(events) == f"The person is {RAW_VALUE}."

    async def test_admin_matches_documents_router(self, chat_env: ChatEnv) -> None:
        await chat_env.set_require_approval(False)
        alice_doc = await chat_env.add_document(chat_env.users["alice"], "a.pdf", ["alpha"])

        chat_env.act_as("admin")
        doc_resp = await chat_env.client.get(f"/api/documents/{alice_doc}")
        status_code, _, _ = await chat_env.ask({"message": "who?", "document_ids": [alice_doc]})

        assert doc_resp.status_code == 200
        assert status_code == 200


class TestAutoRagOwnerScope:
    async def test_regular_user_does_not_retrieve_ownerless_documents(
        self, chat_env: ChatEnv
    ) -> None:
        await chat_env.set_require_approval(False)
        await chat_env.add_document(None, "legacy.pdf", ["ownerless secret chunk"])
        await chat_env.add_document(chat_env.users["bob"], "bob.pdf", ["bob own chunk"])

        chat_env.act_as("alice")
        _, events, _ = await chat_env.ask({"message": "anything?"})

        assert _meta(events)["rag_mode"] == "none"
        assert all("ownerless" not in p for p in chat_env.prompts)

    async def test_regular_user_retrieves_only_own_documents(self, chat_env: ChatEnv) -> None:
        await chat_env.set_require_approval(False)
        await chat_env.add_document(None, "legacy.pdf", ["ownerless secret chunk"])
        own = await chat_env.add_document(chat_env.users["alice"], "alice.pdf", ["alice chunk"])

        chat_env.act_as("alice")
        _, events, _ = await chat_env.ask({"message": "anything?"})

        assert _meta(events)["matched_document_ids"] == [own]
        assert "alice chunk" in chat_env.prompts[-1]
        assert "ownerless" not in chat_env.prompts[-1]


class TestFilenameNotSentToCloud:
    async def test_multi_document_prompt_uses_index_labels(self, chat_env: ChatEnv) -> None:
        await chat_env.set_require_approval(False)
        first = await chat_env.add_document(
            chat_env.users["alice"], "Jane_Roe_medical_record.pdf", ["first chunk"]
        )
        second = await chat_env.add_document(
            chat_env.users["alice"], "salary_John_Doe.xlsx", ["second chunk"]
        )

        chat_env.act_as("alice")
        _, events, _ = await chat_env.ask(
            {"message": "summarise", "document_ids": [first, second]}
        )

        prompt = chat_env.prompts[-1]
        assert "Jane_Roe_medical_record" not in prompt
        assert "salary_John_Doe" not in prompt
        assert "--- Document 1 ---" in prompt
        assert "--- Document 2 ---" in prompt
        names = {d["name"] for d in _meta(events)["matched_documents"]}
        assert names == {"Jane_Roe_medical_record.pdf", "salary_John_Doe.xlsx"}


class TestApprovalFloor:
    @pytest.mark.parametrize(
        ("settings_flag", "request_flag", "expected"),
        [
            (True, False, True),
            (True, None, True),
            (True, True, True),
            (False, True, True),
            (False, False, False),
            (False, None, False),
        ],
    )
    def test_request_can_only_tighten(
        self, settings_flag: bool, request_flag: Optional[bool], expected: bool
    ) -> None:
        assert (
            chat_module._effective_require_approval(settings_flag, request_flag, "manual")
            is expected
        )

    async def test_settings_on_request_off_still_gates(self, chat_env: ChatEnv) -> None:
        await chat_env.set_require_approval(True)
        doc = await chat_env.add_document(chat_env.users["alice"], "a.pdf", ["alpha"])

        chat_env.act_as("alice")
        _, events, _ = await chat_env.ask(
            {"message": "who?", "document_ids": [doc], "require_approval": False}
        )

        types = [e["type"] for e in events]
        assert _meta(events)["require_approval"] is True
        assert "approval_required" in types
        assert "approval_rejected" in types
        assert chat_env.prompts == []

    async def test_pre_approved_chunks_do_not_bypass_gate(self, chat_env: ChatEnv) -> None:
        await chat_env.set_require_approval(True)

        chat_env.act_as("alice")
        _, events, _ = await chat_env.ask(
            {
                "message": "who?",
                "require_approval": False,
                "pre_approved_chunks": [{"text": "client supplied text"}],
            }
        )

        assert [c["masked_chunks"] for c in chat_env.gate.created] == [["client supplied text"]]
        assert "approval_rejected" in [e["type"] for e in events]
        assert chat_env.prompts == []

    async def test_retry_with_foreign_document_id_is_404(self, chat_env: ChatEnv) -> None:
        await chat_env.set_require_approval(False)
        alice_doc = await chat_env.add_document(chat_env.users["alice"], "a.pdf", ["alpha"])

        chat_env.act_as("bob")
        foreign = await chat_env.ask(
            {
                "message": "who?",
                "pre_approved_chunks": [{"text": "alpha", "document_id": alice_doc}],
            }
        )
        missing = await chat_env.ask(
            {
                "message": "who?",
                "pre_approved_chunks": [{"text": "alpha", "document_id": alice_doc + 999}],
            }
        )

        assert foreign[0] == missing[0] == 404
        assert foreign[2] == missing[2]
        assert chat_env.prompts == []
        assert chat_env.gate.created == []

    async def test_empty_pre_approved_list_skips_retrieval(self, chat_env: ChatEnv) -> None:
        await chat_env.set_require_approval(False)
        doc = await chat_env.add_document(chat_env.users["alice"], "a.pdf", ["alpha chunk"])

        chat_env.act_as("alice")
        _, first, _ = await chat_env.ask(
            {"message": "who?", "document_ids": [doc], "require_approval": True}
        )
        token = next(e for e in first if e["type"] == "approval_required")["numbering_token"]
        _, events, _ = await chat_env.ask(
            {
                "message": "who?",
                "document_ids": [doc],
                "pre_approved_chunks": [],
                "numbering_token": token,
            }
        )

        assert _meta(events)["retrieved_chunk_count"] == 0
        assert "alpha chunk" not in chat_env.prompts[-1]


class TestChatDebugStore:
    async def test_debug_record_holds_masked_fields_only(self, chat_env: ChatEnv) -> None:
        await chat_env.set_require_approval(False)
        doc = await chat_env.add_document(chat_env.users["admin"], "a.pdf", ["alpha"])

        chat_env.act_as("admin")
        _, events, _ = await chat_env.ask(
            {"message": "who?", "document_ids": [doc], "session_id": "client-chosen"}
        )
        debug_id = _meta(events)["debug_id"]

        assert debug_id != "client-chosen"
        assert len(debug_id) >= 32
        resp = await chat_env.client.get(f"/api/chat/debug/{debug_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"debug_id", "masked_prompt", "masked_answer"}
        assert PLACEHOLDER in body["masked_answer"]
        assert RAW_VALUE not in json.dumps(body)
        assert (await chat_env.client.get("/api/chat/debug/client-chosen")).status_code == 404

    async def test_debug_ids_are_unique_per_turn(self, chat_env: ChatEnv) -> None:
        await chat_env.set_require_approval(False)
        doc = await chat_env.add_document(chat_env.users["admin"], "a.pdf", ["alpha"])

        chat_env.act_as("admin")
        payload = {"message": "who?", "document_ids": [doc], "session_id": "same"}
        first = _meta((await chat_env.ask(payload))[1])["debug_id"]
        second = _meta((await chat_env.ask(payload))[1])["debug_id"]

        assert first != second

    def test_store_scopes_records_to_owner(self) -> None:
        debug_id = chat_debug_store.new_debug_id()
        chat_debug_store.set_chat_debug_record(debug_id, 7, "prompt", "answer")

        assert chat_debug_store.get_chat_debug_record(debug_id, 7, False) is not None
        assert chat_debug_store.get_chat_debug_record(debug_id, 8, False) is None
        assert chat_debug_store.get_chat_debug_record(debug_id, 8, True) is not None
        assert chat_debug_store.get_chat_debug_record("unknown", 7, False) is None


class TestDebugAccessOverHttp:
    async def _alice_debug_id(self, chat_env: ChatEnv) -> str:
        await chat_env.set_require_approval(False)
        doc = await chat_env.add_document(chat_env.users["alice"], "a.pdf", ["alpha"])
        chat_env.act_as("alice")
        _, events, _ = await chat_env.ask({"message": "who?", "document_ids": [doc]})
        return _meta(events)["debug_id"]

    async def test_other_user_gets_same_404_as_unknown_id(self, chat_env: ChatEnv) -> None:
        debug_id = await self._alice_debug_id(chat_env)

        chat_env.act_as("bob")
        foreign = await chat_env.client.get(f"/api/chat/debug/{debug_id}")
        missing = await chat_env.client.get("/api/chat/debug/" + "0" * 32)

        assert foreign.status_code == 404
        assert missing.status_code == 404
        assert foreign.json() == missing.json()
        assert "masked_prompt" not in foreign.text

    async def test_owner_reads_own_masked_record(self, chat_env: ChatEnv) -> None:
        debug_id = await self._alice_debug_id(chat_env)

        chat_env.act_as("alice")
        resp = await chat_env.client.get(f"/api/chat/debug/{debug_id}")

        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"debug_id", "masked_prompt", "masked_answer"}
        assert body["debug_id"] == debug_id
        assert RAW_VALUE not in json.dumps(body)

    async def test_admin_reads_any_record_and_unknown_is_404(self, chat_env: ChatEnv) -> None:
        debug_id = await self._alice_debug_id(chat_env)

        chat_env.act_as("admin")
        resp = await chat_env.client.get(f"/api/chat/debug/{debug_id}")
        missing = await chat_env.client.get("/api/chat/debug/" + "0" * 32)

        assert resp.status_code == 200
        assert resp.json()["debug_id"] == debug_id
        assert missing.status_code == 404

    def test_store_reports_foreign_record_as_missing(self) -> None:
        debug_id = chat_debug_store.new_debug_id()
        chat_debug_store.set_chat_debug_record(debug_id, 1, "p", "a")

        assert chat_debug_store.get_chat_debug_record(debug_id, 2, False) is None
        assert chat_debug_store.get_chat_debug_record("missing", 2, False) is None


class TestForeignChunkPatch:
    async def test_patch_on_foreign_document_is_404(self, chat_env: ChatEnv) -> None:
        doc = await chat_env.add_document(chat_env.users["alice"], "a.pdf", ["alpha"])
        async with chat_env.session_maker() as session:
            chunk_id = (
                await session.execute(select(Chunk.id).where(Chunk.document_id == doc))
            ).scalar_one()

        chat_env.act_as("bob")
        foreign = await chat_env.client.patch(
            f"/api/chunks/{chunk_id}", json={"sanitized_text": "changed"}
        )
        missing = await chat_env.client.patch(
            "/api/chunks/999999", json={"sanitized_text": "changed"}
        )

        assert foreign.status_code == 404
        assert foreign.json() == missing.json()
        async with chat_env.session_maker() as session:
            text = (
                await session.execute(select(Chunk.sanitized_text).where(Chunk.id == chunk_id))
            ).scalar_one()
        assert text == "alpha"


class TestApprovalSessionOwnership:
    async def test_foreign_session_id_is_not_overwritten(
        self, chat_env: ChatEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gate = ApprovalGate()
        await gate.create(
            session_id="shared",
            masked_prompt="bob's question",
            masked_chunks=["bob chunk"],
            entity_count=0,
            owner_user_id=chat_env.users["bob"].id,
        )
        monkeypatch.setattr(chat_module, "get_approval_gate", lambda: gate)
        await chat_env.set_require_approval(True)
        doc = await chat_env.add_document(chat_env.users["alice"], "a.pdf", ["alpha"])

        chat_env.act_as("alice")
        _, events, _ = await chat_env.ask(
            {"message": "who?", "document_ids": [doc], "session_id": "shared"}
        )

        errors = [e for e in events if e["type"] == "error"]
        assert errors and errors[0]["message"] == "Approval session not found."
        assert not [e for e in events if e["type"] == "approval_required"]
        assert chat_env.prompts == []
        await gate.assert_session_owner("shared", chat_env.users["bob"].id)
        assert await gate.get_assembly_context("shared") is None

    async def test_status_endpoint_hides_foreign_session(self, chat_env: ChatEnv) -> None:
        gate = ApprovalGate()
        await gate.create(
            session_id="bobs",
            masked_prompt="q",
            masked_chunks=[],
            entity_count=0,
            owner_user_id=chat_env.users["bob"].id,
        )
        from septum_api.routers import approval as approval_router

        original = approval_router._get_gate
        approval_router._get_gate = lambda: gate
        try:
            chat_env.act_as("alice")
            foreign = await chat_env.client.get("/api/approval/bobs")
            missing = await chat_env.client.get("/api/approval/nope")
            chat_env.act_as("bob")
            own = await chat_env.client.get("/api/approval/bobs")
            chat_env.act_as("admin")
            admin = await chat_env.client.get("/api/approval/bobs")
        finally:
            approval_router._get_gate = original

        assert foreign.status_code == 404
        assert foreign.json() == missing.json()
        assert own.status_code == 200
        assert admin.status_code == 200


class TestPreApprovedLimits:
    async def test_too_many_chunks_is_422(self, chat_env: ChatEnv) -> None:
        chat_env.act_as("alice")
        status_code, _, _ = await chat_env.ask(
            {
                "message": "who?",
                "pre_approved_chunks": [{"text": "x"}] * (request_limits.MAX_CONTEXT_CHUNKS + 1),
            }
        )
        assert status_code == 422
        assert chat_env.prompts == []

    async def test_oversized_chunk_text_is_422(self, chat_env: ChatEnv) -> None:
        chat_env.act_as("alice")
        status_code, _, _ = await chat_env.ask(
            {
                "message": "who?",
                "pre_approved_chunks": [
                    {"text": "x" * (request_limits.MAX_PRE_APPROVED_CHUNK_CHARS + 1)}
                ],
            }
        )
        assert status_code == 422

    async def test_distinct_document_ids_are_capped_after_dedupe(
        self, chat_env: ChatEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await chat_env.set_require_approval(False)
        alice = chat_env.users["alice"]
        doc_a = await chat_env.add_document(alice, "a.pdf", ["alpha"])
        doc_b = await chat_env.add_document(alice, "b.pdf", ["beta"])
        monkeypatch.setattr(chat_module, "MAX_RETRY_DOCUMENTS", 1)
        chat_env.act_as("alice")

        same_doc = await chat_env.ask(
            {
                "message": "who?",
                "pre_approved_chunks": [
                    {"text": "alpha", "document_id": doc_a},
                    {"text": "alpha again", "document_id": doc_a},
                ],
            }
        )
        two_docs = await chat_env.ask(
            {
                "message": "who?",
                "pre_approved_chunks": [
                    {"text": "alpha", "document_id": doc_a},
                    {"text": "beta", "document_id": doc_b},
                ],
            }
        )

        assert same_doc[0] == 200
        assert two_docs[0] == 422

    def test_offer_is_capped_evenly_across_documents(self) -> None:
        chunks = [Chunk(id=i, document_id=1 + i // 10, index=i) for i in range(30)]

        kept = chat_module._cap_context_chunks(chunks, 7)

        assert [c.document_id for c in kept] == [1, 1, 1, 2, 2, 3, 3]
        assert kept == sorted(kept, key=lambda c: c.id)


class TestAnalyzeQueryOwnerScope:
    async def test_admin_picker_excludes_other_users_documents(
        self, chat_env: ChatEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        admin = chat_env.users["admin"]
        own = await chat_env.add_document(admin, "admin.pdf", ["a"])
        legacy = await chat_env.add_document(None, "legacy.pdf", ["l"])
        foreign = await chat_env.add_document(chat_env.users["bob"], "bob_private.pdf", ["b"])

        async def _sanitize(message, language, settings, anon_map, db):
            anon_map.add_entity(RAW_VALUE, "PERSON_NAME")
            return message, 1, {"PERSON_NAME": 1}

        async def _scores(db, entities, owner_id=None):
            return {own: 1.0, legacy: 1.0, foreign: 1.0}

        async def _clusters(db, ids):
            return [[d] for d in sorted(ids)]

        monkeypatch.setattr(chat_module, "_sanitize_query", _sanitize)
        monkeypatch.setattr(chat_module, "find_documents_for_query_entities", _scores)
        monkeypatch.setattr(chat_module, "cluster_documents_by_relationship", _clusters)
        chat_env.act_as("admin")

        resp = await chat_env.client.post(
            "/api/chat/analyze_query", json={"message": f"Who is {RAW_VALUE}?"}
        )

        assert resp.status_code == 200
        body = resp.json()
        listed = {d for c in body["clusters"] for d in c["document_ids"]}
        assert listed == {own, legacy}
        assert foreign not in body["narrowed_doc_ids"]
        assert "bob_private.pdf" not in resp.text


class TestNoContextTurn:
    def test_no_context_turn_is_never_gated(self) -> None:
        assert chat_module._effective_require_approval(True, True, "none") is False

    async def test_no_documents_means_no_gate_and_no_chunks(self, chat_env: ChatEnv) -> None:
        await chat_env.set_require_approval(True)
        await chat_env.add_document(chat_env.users["bob"], "b.pdf", ["bob secret chunk"])
        chat_env.act_as("alice")

        _, events, _ = await chat_env.ask({"message": "hello"})

        assert _meta(events)["rag_mode"] == "none"
        assert _meta(events)["require_approval"] is False
        assert "approval_required" not in [e["type"] for e in events]
        assert chat_env.gate.created == []
        assert len(chat_env.prompts) == 1
        assert "bob secret chunk" not in chat_env.prompts[0]

    async def test_chat_intent_sends_no_chunks(
        self, chat_env: ChatEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _chat_intent(**kwargs):
            return "chat"

        monkeypatch.setattr(chat_module, "_classify_query_intent", _chat_intent)
        await chat_env.set_require_approval(True)
        await chat_env.add_document(chat_env.users["alice"], "a.pdf", ["alice own chunk"])
        chat_env.act_as("alice")

        _, events, _ = await chat_env.ask({"message": "hello"})

        assert _meta(events)["rag_mode"] == "none"
        assert _meta(events)["retrieved_chunk_count"] == 0
        assert chat_env.gate.created == []
        assert "alice own chunk" not in chat_env.prompts[0]


class TestApprovalSessionCleanup:
    async def test_stream_closed_right_after_approval_required_pops_the_session(
        self, chat_env: ChatEnv
    ) -> None:
        from starlette.requests import Request

        gate = ApprovalGate()
        chat_env.gate = gate
        await chat_env.set_require_approval(True)
        doc = await chat_env.add_document(chat_env.users["alice"], "a.pdf", ["alpha"])
        http_request = Request({"type": "http", "method": "POST", "path": "/api/chat/ask", "headers": []})

        async with chat_env.session_maker() as db:
            response = await chat_module.chat_ask(
                chat_module.ChatRequest(message="who?", document_ids=[doc]),
                http_request,
                db=db,
                current_user=chat_env.users["alice"],
            )
            stream = response.body_iterator
            async for chunk in stream:
                if b"approval_required" in chunk:
                    break
            assert len(gate._sessions) == 1
            session = next(iter(gate._sessions.values()))

            await stream.aclose()

        assert gate._sessions == {}
        assert session.anon_map is None


class TestLlmErrorEvent:
    async def _ask_with_failure(
        self, chat_env: ChatEnv, monkeypatch: pytest.MonkeyPatch, error: LLMRouterError
    ) -> dict[str, Any]:
        await chat_env.set_require_approval(False)
        doc = await chat_env.add_document(chat_env.users["alice"], "a.pdf", ["alpha"])

        async def _fail(self, messages, **kwargs):
            raise error

        monkeypatch.setattr(LLMRouter, "complete", _fail)
        chat_env.act_as("alice")
        _, events, _ = await chat_env.ask({"message": "who?", "document_ids": [doc]})
        return next(e for e in events if e["type"] == "error")

    async def test_error_event_carries_the_machine_code(
        self, chat_env: ChatEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        event = await self._ask_with_failure(
            chat_env, monkeypatch, LLMRouterError("refused", code="ollama_url_not_allowed")
        )

        assert event["code"] == "ollama_url_not_allowed"

    async def test_uncoded_error_gets_the_fixed_llm_unavailable_event(
        self, chat_env: ChatEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        upstream = "gateway: upstream 502 from https://internal.example:8443 key=sk-123"
        event = await self._ask_with_failure(chat_env, monkeypatch, LLMRouterError(upstream))

        assert event["code"] == chat_module.LLM_UNAVAILABLE == "llm_unavailable"
        assert event["message"] == chat_module.LLM_UNAVAILABLE_MESSAGE
        for fragment in ("gateway", "502", "internal.example", "sk-123"):
            assert fragment not in event["message"]

    @pytest.mark.parametrize(
        ("error", "expected_code"),
        [(LLMRouterError("down"), "llm_unavailable"), (RuntimeError("unexpected"), None)],
    )
    async def test_failing_error_log_is_reported_and_the_error_event_still_arrives(
        self, chat_env: ChatEnv, monkeypatch: pytest.MonkeyPatch, error: Exception, expected_code: Optional[str]
    ) -> None:
        from septum_api.services.error_logger import ERROR_LOG_WRITE_FAILURES

        async def _broken(*args, **kwargs):
            raise OSError("db gone")

        monkeypatch.setattr(chat_module, "log_backend_error", _broken)
        before = ERROR_LOG_WRITE_FAILURES._value.get()

        event = await self._ask_with_failure(chat_env, monkeypatch, error)

        assert event["type"] == "error"
        if expected_code:
            assert event["code"] == expected_code
        assert ERROR_LOG_WRITE_FAILURES._value.get() == before + 1

    async def test_failing_error_log_does_not_break_a_cloud_fallback_turn(
        self, chat_env: ChatEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from septum_api.services.error_logger import ERROR_LOG_WRITE_FAILURES

        async def _broken(*args, **kwargs):
            raise OSError("db gone")

        async def _complete(self, messages, **kwargs):
            await kwargs["on_cloud_failure"]("cloud down", {})
            return "fallback answer"

        await chat_env.set_require_approval(False)
        doc = await chat_env.add_document(chat_env.users["alice"], "a.pdf", ["alpha"])
        monkeypatch.setattr(chat_module, "log_backend_message", _broken)
        monkeypatch.setattr(LLMRouter, "complete", _complete)
        before = ERROR_LOG_WRITE_FAILURES._value.get()
        chat_env.act_as("alice")

        _, events, _ = await chat_env.ask({"message": "who?", "document_ids": [doc]})

        assert not [e for e in events if e["type"] == "error"]
        assert ERROR_LOG_WRITE_FAILURES._value.get() == before + 1

    async def test_coded_error_keeps_its_authored_message(
        self, chat_env: ChatEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from septum_api.services.ollama_client import (
            OLLAMA_URL_NOT_ALLOWED_CODE,
            OLLAMA_URL_REFUSED_MESSAGE,
        )

        event = await self._ask_with_failure(
            chat_env,
            monkeypatch,
            LLMRouterError(OLLAMA_URL_REFUSED_MESSAGE, code=OLLAMA_URL_NOT_ALLOWED_CODE),
        )

        assert event["code"] == OLLAMA_URL_NOT_ALLOWED_CODE
        assert event["message"] == OLLAMA_URL_REFUSED_MESSAGE
