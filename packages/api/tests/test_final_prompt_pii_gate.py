from __future__ import annotations

"""Regression tests for the final-prompt PII gate and map hygiene.

Covers text that never came from stored ``sanitized_text`` (approval-modal
edits, client ``pre_approved_chunks``, inline chunk edits, spreadsheet
labels, custom regulation names), the shared per-document map not being
mutated by a chat query, and de-anonymization limited to placeholders the
turn exposed. Detection uses the real Presidio layer (NER and Ollama
layers off); the cloud LLM call is mocked at ``LLMRouter.complete``, the
single entry point for both direct providers and the gateway. Without a
composed policy Presidio reports names as ``PERSON``; production routes
names through the NER layer, which the gate calls the same way.
"""

import copy
import json
import re
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import pytest
from httpx import ASGITransport, AsyncClient
from septum_core.spans import ResolvedSpan, SanitizeResult
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from septum_api.database import build_default_app_settings, get_db
from septum_api.main import app
from septum_api.models import Base
from septum_api.models.document import Chunk, Document
from septum_api.models.regulation import RegulationRuleset
from septum_api.models.spreadsheet_schema import SpreadsheetColumn, SpreadsheetSchema
from septum_api.models.user import User
from septum_api.routers import chat as chat_module
from septum_api.services import document_anon_store, pii_gate
from septum_api.services.anonymization_map import AnonymizationMap
from septum_api.services.approval_gate import ApprovalChunk, ApprovalDecision
from septum_api.services.llm_router import LLMRouter
from septum_api.services.policy_snapshot import current_policy_snapshot
from septum_api.services.sanitizer import PIISanitizer
from septum_api.utils.auth_dependency import get_current_user

NAME = "Michael Thompson"
IBAN = "DE89370400440532013000"
KNOWN_NAME = "Jane Roe"
OTHER_NAME = "John Doe"
_PLACEHOLDER_RE = re.compile(r"\[[A-Z][A-Z_]*_\d+\]")


def _presidio_only_settings():
    settings = build_default_app_settings()
    settings.use_ner_layer = False
    settings.use_ollama_layer = False
    settings.use_ollama_validation_layer = False
    settings.use_ollama_semantic_layer = False
    return settings


class _ApprovingGate:
    """Approval gate double that approves with chunks produced by ``edit``."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.edit = lambda texts: texts

    async def create(self, **kwargs: Any) -> None:
        self.created.append(kwargs)

    def discard_session(self, session_id: str, handle: Any) -> None:
        pass

    async def wait_for_approval(self, session_id: str, timeout: float) -> ApprovalDecision:
        texts = self.edit(list(self.created[-1]["masked_chunks"]))
        return ApprovalDecision(
            approved=True,
            chunks=[ApprovalChunk(id=None, document_id=None, text=t) for t in texts],
        )


class _Env:
    def __init__(self, session_maker: Any, client: AsyncClient, user: User) -> None:
        self.session_maker = session_maker
        self.client = client
        self.user = user
        self.prompts: list[str] = []
        self.gate = _ApprovingGate()
        self.answer: Optional[str] = None

    async def set_require_approval(self, value: bool) -> None:
        async with self.session_maker() as session:
            settings = _presidio_only_settings()
            settings.require_approval = value
            session.add(settings)
            await session.commit()

    async def add_document(self, chunk_texts: list[str]) -> int:
        async with self.session_maker() as session:
            doc = Document(
                filename="stored.pdf",
                original_filename="report.pdf",
                file_type="application/pdf",
                file_format="pdf",
                detected_language="en",
                encrypted_path="/nonexistent",
                file_size_bytes=10,
                ocr_confidence=0.0,
                active_regulation_ids=[],
                sanitized_entity_types=[],
                sanitized_policy_digest=(await current_policy_snapshot(session)).digest,
                sanitized_language="en",
                ingestion_status="completed",
                chunk_count=len(chunk_texts),
                user_id=self.user.id,
            )
            session.add(doc)
            await session.flush()
            for idx, text in enumerate(chunk_texts):
                session.add(
                    Chunk(document_id=doc.id, index=idx, sanitized_text=text, char_count=len(text))
                )
            await session.commit()
            return doc.id

    async def ask(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        resp = await self.client.post("/api/chat/ask", json=payload)
        assert resp.status_code == 200, resp.text
        return [
            json.loads(line[len("data: "):])
            for line in resp.text.splitlines()
            if line.startswith("data: ")
        ]


@pytest.fixture
async def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Env]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gate.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _override_get_db():
        async with session_maker() as session:
            yield session

    async with session_maker() as session:
        user = User(email="owner@example.com", hashed_password="x", role="editor")
        session.add(user)
        await session.commit()

    sanitizer = PIISanitizer(settings=_presidio_only_settings())

    async def _create_sanitizer(db, settings, enable_ollama=False, policy=None):
        return sanitizer

    async def _retrieve(db, document_id, query, top_k, chunk_count, relevance_threshold):
        rows = await db.execute(select(Chunk).where(Chunk.document_id == document_id))
        return list(rows.scalars().all())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        env = _Env(session_maker, client, user)

        async def _complete(self, messages, **kwargs):
            prompt = messages[-1]["content"]
            env.prompts.append(prompt)
            if env.answer is not None:
                return env.answer
            return "Seen: " + " ".join(sorted(set(_PLACEHOLDER_RE.findall(prompt))))

        monkeypatch.setattr(document_anon_store, "_ANON_MAP_DIR", tmp_path / "maps")
        monkeypatch.setattr(document_anon_store, "_document_maps", {})
        monkeypatch.setattr(chat_module, "create_sanitizer", _create_sanitizer)
        monkeypatch.setattr(pii_gate, "create_sanitizer", _create_sanitizer)
        monkeypatch.setattr(chat_module, "_retrieve_chunks", _retrieve)
        monkeypatch.setattr(chat_module, "get_approval_gate", lambda: env.gate)
        monkeypatch.setattr(LLMRouter, "complete", _complete)

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user] = lambda: env.user
        try:
            yield env
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)
            await engine.dispose()


def _answer(events: list[dict[str, Any]]) -> str:
    assert not [e for e in events if e["type"] == "error"], events
    return "".join(e["text"] for e in events if e["type"] == "answer_chunk")


def _assert_masked(text: str) -> None:
    assert NAME not in text
    assert IBAN not in text
    assert "Thompson" not in text


async def _store_known_map(document_id: int) -> AnonymizationMap:
    amap = AnonymizationMap(document_id=document_id, language="en")
    amap.add_entity(KNOWN_NAME, "PERSON_NAME")
    amap.add_entity(OTHER_NAME, "PERSON_NAME")
    await document_anon_store.set_document_map(document_id, amap)
    return amap


class TestApprovalEdits:
    async def test_edited_chunk_is_gated_and_restored(self, env: _Env) -> None:
        await env.set_require_approval(True)
        doc = await env.add_document(["[PERSON_NAME_1] signed the contract."])
        await _store_known_map(doc)
        env.gate.edit = lambda texts: [
            texts[0] + f" Payment by {NAME} to IBAN {IBAN}, approved by {KNOWN_NAME}."
        ]

        answer = _answer(await env.ask({"message": "Who signed?", "document_ids": [doc]}))

        prompt = env.prompts[-1]
        _assert_masked(prompt)
        assert KNOWN_NAME not in prompt
        assert NAME in answer
        assert IBAN in answer
        assert KNOWN_NAME in answer

    async def test_unedited_chunks_are_passed_through(self, env: _Env) -> None:
        await env.set_require_approval(True)
        doc = await env.add_document(["[PERSON_NAME_1] signed the contract."])
        await _store_known_map(doc)

        await env.ask({"message": "Who signed?", "document_ids": [doc]})

        assert "[PERSON_NAME_1] signed the contract." in env.prompts[-1]


class TestPreApprovedChunks:
    async def test_client_chunks_are_gated_and_restored(self, env: _Env) -> None:
        await env.set_require_approval(False)

        answer = _answer(
            await env.ask(
                {
                    "message": "Summarise the payment.",
                    "pre_approved_chunks": [
                        {"text": f"Transfer from {NAME} to IBAN {IBAN} is pending."}
                    ],
                }
            )
        )

        _assert_masked(env.prompts[-1])
        assert NAME in answer
        assert IBAN in answer

    async def test_gated_client_chunks_are_what_the_approval_modal_shows(
        self, env: _Env
    ) -> None:
        await env.set_require_approval(True)

        await env.ask(
            {
                "message": "Summarise the payment.",
                "pre_approved_chunks": [{"text": f"{NAME} pays IBAN {IBAN}."}],
            }
        )

        sent_to_modal = {k: v for k, v in env.gate.created[-1].items() if k != "anon_map"}
        _assert_masked(json.dumps(sent_to_modal))


class TestChunkPatch:
    async def test_patch_stores_masked_text_and_extends_encrypted_map(
        self, env: _Env, tmp_path: Path
    ) -> None:
        await env.set_require_approval(False)
        doc = await env.add_document(["[PERSON_NAME_1] signed the contract."])
        await _store_known_map(doc)
        async with env.session_maker() as session:
            chunk_id = (
                await session.execute(select(Chunk.id).where(Chunk.document_id == doc))
            ).scalar_one()

        resp = await env.client.patch(
            f"/api/chunks/{chunk_id}",
            json={"sanitized_text": f"{KNOWN_NAME} wired funds to {NAME}, IBAN {IBAN}."},
        )

        assert resp.status_code == 200
        stored = resp.json()["sanitized_text"]
        _assert_masked(stored)
        assert KNOWN_NAME not in stored
        assert stored.startswith("[PERSON_NAME_1] wired funds to ")
        raw_file = (tmp_path / "maps" / f"{doc}.enc").read_bytes()
        assert NAME.encode() not in raw_file
        assert IBAN.encode() not in raw_file

        document_anon_store._document_maps.clear()
        reloaded = await document_anon_store.get_document_map(doc)
        assert reloaded is not None
        assert NAME in reloaded.entity_map
        assert IBAN in reloaded.entity_map
        assert reloaded.entity_map[KNOWN_NAME] == "[PERSON_NAME_1]"
        assert len(set(reloaded.entity_map.values())) == len(reloaded.entity_map)

        answer = _answer(await env.ask({"message": "Who was paid?", "document_ids": [doc]}))
        _assert_masked(env.prompts[-1])
        assert NAME in answer
        assert IBAN in answer

    async def test_patch_without_stored_map_does_not_reuse_placeholders(
        self, env: _Env
    ) -> None:
        await env.set_require_approval(False)
        doc = await env.add_document(
            ["[PERSON_1] and [PERSON_2] signed.", "Second chunk."]
        )
        async with env.session_maker() as session:
            chunk_id = (
                await session.execute(
                    select(Chunk.id).where(Chunk.document_id == doc, Chunk.index == 1)
                )
            ).scalar_one()

        resp = await env.client.patch(
            f"/api/chunks/{chunk_id}", json={"sanitized_text": f"Payee {NAME}."}
        )

        stored = resp.json()["sanitized_text"]
        _assert_masked(stored)
        assert "[PERSON_1]" not in stored
        assert "[PERSON_2]" not in stored


class TestOtherUserTypedPromptInputs:
    async def test_spreadsheet_label_is_gated_on_write(self, env: _Env) -> None:
        await env.set_require_approval(False)
        doc = await env.add_document(["[PERSON_NAME_1] earns 10."])
        await _store_known_map(doc)
        async with env.session_maker() as session:
            schema = SpreadsheetSchema(document_id=doc)
            session.add(schema)
            await session.flush()
            session.add(
                SpreadsheetColumn(
                    schema_id=schema.id, index=0, technical_label="COLUMN_1", is_numeric=True
                )
            )
            await session.commit()

        resp = await env.client.put(
            f"/api/documents/{doc}/schema",
            json={
                "columns": [
                    {
                        "index": 0,
                        "technical_label": "COLUMN_1",
                        "semantic_label": f"Salary paid to {NAME}",
                        "is_numeric": True,
                    }
                ]
            },
        )

        assert resp.status_code == 200
        _assert_masked(resp.text)
        await env.ask({"message": "What is the salary?", "document_ids": [doc]})
        _assert_masked(env.prompts[-1])

    async def test_custom_regulation_name_is_gated(self, env: _Env) -> None:
        await env.set_require_approval(False)
        doc = await env.add_document(["[PERSON_NAME_1] signed."])
        async with env.session_maker() as session:
            session.add_all(
                [
                    RegulationRuleset(
                        id="gdpr", display_name="GDPR", region="EU", is_builtin=True,
                        is_active=True, entity_types=[],
                    ),
                    RegulationRuleset(
                        id="custom_1", display_name=f"Policy of {NAME}", region="",
                        is_builtin=False, is_active=True, entity_types=[],
                    ),
                ]
            )
            await session.commit()

        events = await env.ask({"message": "Who signed?", "document_ids": [doc]})

        prompt = env.prompts[-1]
        _assert_masked(prompt)
        assert "GDPR" in prompt
        meta = next(e for e in events if e["type"] == "meta")
        assert f"Policy of {NAME}" in meta["active_regulations"]


class TestSharedDocumentMap:
    async def test_chat_query_does_not_mutate_cached_document_map(self, env: _Env) -> None:
        await env.set_require_approval(False)
        doc = await env.add_document(["[PERSON_NAME_1] signed the contract."])
        await _store_known_map(doc)
        cached = await document_anon_store.get_document_map(doc)
        snapshot = copy.deepcopy(cached)

        answer = _answer(
            await env.ask(
                {
                    "message": f"Did {NAME} send money from IBAN {IBAN}?",
                    "document_ids": [doc],
                }
            )
        )

        after = await document_anon_store.get_document_map(doc)
        assert after is cached
        assert after.entity_map == snapshot.entity_map
        assert after.token_to_placeholder == snapshot.token_to_placeholder
        assert after.token_counter == snapshot.token_counter
        assert after.blocklist == snapshot.blocklist
        assert after.placeholder_lookup == snapshot.placeholder_lookup
        _assert_masked(env.prompts[-1])
        assert NAME in answer


class TestDeanonScope:
    async def test_placeholder_not_in_prompt_stays_literal(self, env: _Env) -> None:
        await env.set_require_approval(False)
        doc = await env.add_document(["[PERSON_NAME_1] signed the contract."])
        await _store_known_map(doc)
        env.answer = "[PERSON_NAME_1] and [PERSON_NAME_2]"

        answer = _answer(await env.ask({"message": "Who signed?", "document_ids": [doc]}))

        assert "[PERSON_NAME_2]" not in env.prompts[-1]
        assert answer == f"{KNOWN_NAME} and [PERSON_NAME_2]"
        assert OTHER_NAME not in answer


class _SpanSanitizer:
    """Sanitizer double that reports fixed substrings as entities."""

    def __init__(self, targets: list[tuple[str, str]]) -> None:
        self._targets = targets

    def sanitize(self, text: str, language: str, anon_map: AnonymizationMap) -> SanitizeResult:
        spans = []
        for value, entity_type in self._targets:
            start = text.find(value)
            if start >= 0:
                spans.append(
                    ResolvedSpan(start, start + len(value), entity_type, "[X_1]", 1.0)
                )
        return SanitizeResult(sanitized_text=text, entity_count=len(spans), detected_spans=spans)


class TestGateUnit:
    def test_numeric_original_needs_digit_boundaries(self) -> None:
        amap = AnonymizationMap(document_id=0, language="en")
        amap.add_entity("3/15", "DATE")
        amap.add_entity("4711", "MEDICAL_RECORD_NUMBER")

        out = pii_gate.mask_known_entities(
            "Seen 13/150, 3/15 and (3/15); ids 47110, A4711, MRN4711, 4711.", amap
        )

        assert out == (
            "Seen 13/150, [DATE_1] and ([DATE_1]); ids 47110, A[MEDICAL_RECORD_NUMBER_1], "
            "MRN[MEDICAL_RECORD_NUMBER_1], [MEDICAL_RECORD_NUMBER_1]."
        )

    @pytest.mark.parametrize(
        ("original", "entity", "text", "expected"),
        [
            (
                "\u0664\u0667\u0661\u0661",
                "MEDICAL_RECORD_NUMBER",
                "\u0664\u0667\u0661\u0661, A\u0664\u0667\u0661\u0661, \u0661\u0664\u0667\u0661\u0661",
                "[MEDICAL_RECORD_NUMBER_1], A[MEDICAL_RECORD_NUMBER_1], "
                "\u0661\u0664\u0667\u0661\u0661",
            ),
            ("(555) 1", "PHONE_NUMBER", "5(555) 1 and (555) 12", "5[PHONE_NUMBER_1] and (555) 12"),
        ],
    )
    def test_digit_boundary_only_on_digit_ends(
        self, original: str, entity: str, text: str, expected: str
    ) -> None:
        amap = AnonymizationMap(document_id=0, language="en")
        amap.add_entity(original, entity)

        assert pii_gate.mask_known_entities(text, amap) == expected

    def test_letterless_original_left_inside_placeholder(self) -> None:
        amap = AnonymizationMap(document_id=0, language="en")
        amap.add_entity("3/15", "DATE")
        amap.add_entity("1", "ID_NUMBER")

        assert pii_gate.mask_known_entities("[DATE_1] and 1", amap) == "[DATE_1] and [ID_NUMBER_1]"

    def test_original_with_letters_keeps_substring_matching(self) -> None:
        amap = AnonymizationMap(document_id=0, language="en")
        amap.add_entity("Acme", "ORGANIZATION")

        assert pii_gate.mask_known_entities("AcmeCorp", amap) == "[ORGANIZATION_1]Corp"

    def test_unified_map_without_counters_gets_fresh_placeholder(self) -> None:
        amap = AnonymizationMap(document_id=0, language="en")
        amap.entity_map = {KNOWN_NAME: "[PERSON_NAME_1]"}
        amap.placeholder_lookup = {"[PERSON_NAME_1]": KNOWN_NAME}

        out = pii_gate._gate_one(
            f"Ask {NAME}.", amap, _SpanSanitizer([(NAME, "PERSON_NAME")]), "en"
        )

        assert out == "Ask [PERSON_NAME_2]."
        assert amap.placeholder_lookup["[PERSON_NAME_2]"] == NAME

    def test_span_overlapping_known_placeholder_is_trimmed(self) -> None:
        amap = AnonymizationMap(document_id=0, language="en")
        amap.add_entity(KNOWN_NAME, "PERSON_NAME")

        out = pii_gate._gate_one(
            "Thompson [PERSON_NAME_1] wrote.",
            amap,
            _SpanSanitizer([("Thompson [PERSON_NAME_1]", "PERSON_NAME")]),
            "en",
        )

        assert out == "[PERSON_NAME_2] [PERSON_NAME_1] wrote."

    def test_unknown_placeholder_shaped_text_is_not_protected(self) -> None:
        amap = AnonymizationMap(document_id=0, language="en")

        out = pii_gate._gate_one(
            "See [JANE_ROE_7].", amap, _SpanSanitizer([("JANE_ROE", "PERSON_NAME")]), "en"
        )

        assert "JANE_ROE" not in out

    def test_known_original_is_masked_before_detection(self) -> None:
        amap = AnonymizationMap(document_id=0, language="en")
        amap.add_entity(KNOWN_NAME, "PERSON_NAME")

        out = pii_gate._gate_one(f"Ask {KNOWN_NAME}.", amap, _SpanSanitizer([]), "en")

        assert out == "Ask [PERSON_NAME_1]."

    def test_known_original_is_never_replaced_inside_a_placeholder(self) -> None:
        amap = AnonymizationMap(document_id=0, language="en")
        amap.entity_map = {"IBAN": "[ORGANIZATION_1]", IBAN: "[IBAN_1]"}

        out = pii_gate.mask_known_entities("Pay IBAN [IBAN_1] or IBAN " + IBAN, amap)

        assert out == "Pay [ORGANIZATION_1] [IBAN_1] or [ORGANIZATION_1] [IBAN_1]"


async def _add_schema(env: _Env, doc: int, columns: list[tuple[int, str, Optional[str]]]) -> None:
    async with env.session_maker() as session:
        schema = SpreadsheetSchema(document_id=doc)
        session.add(schema)
        await session.flush()
        for index, technical, semantic in columns:
            session.add(
                SpreadsheetColumn(
                    schema_id=schema.id,
                    index=index,
                    technical_label=technical,
                    semantic_label=semantic,
                    is_numeric=False,
                )
            )
        await session.commit()


class TestTechnicalLabel:
    async def test_new_column_technical_label_is_server_generated(self, env: _Env) -> None:
        await env.set_require_approval(False)
        doc = await env.add_document(["[PERSON_NAME_1] earns 10."])

        resp = await env.client.put(
            f"/api/documents/{doc}/schema",
            json={
                "columns": [
                    {"index": 2, "technical_label": f"Salary of {NAME}", "is_numeric": True}
                ]
            },
        )

        assert resp.status_code == 200
        _assert_masked(resp.text)
        assert resp.json()["columns"][0]["technical_label"] == "COLUMN_3"
        async with env.session_maker() as session:
            stored = (await session.execute(select(SpreadsheetColumn.technical_label))).scalars().all()
        assert stored == ["COLUMN_3"]

    async def test_legacy_raw_labels_are_gated_in_the_prompt(self, env: _Env) -> None:
        await env.set_require_approval(False)
        doc = await env.add_document(["[PERSON_NAME_1] earns 10."])
        await _store_known_map(doc)
        await _add_schema(
            env,
            doc,
            [(0, f"Paid to {KNOWN_NAME}", None), (1, "COLUMN_2", f"Bonus for {NAME}")],
        )

        answer = _answer(await env.ask({"message": "What is the salary?", "document_ids": [doc]}))

        prompt = env.prompts[-1]
        _assert_masked(prompt)
        assert KNOWN_NAME not in prompt
        assert "index 0 → Paid to [PERSON_NAME_1]" in prompt
        assert NAME in answer


class TestDeanonAllowSet:
    async def test_instruction_example_placeholder_is_not_restored(self, env: _Env) -> None:
        await env.set_require_approval(False)
        doc = await env.add_document(["[PERSON_2] signed the contract."])
        amap = AnonymizationMap(document_id=doc, language="en")
        amap.add_entity(KNOWN_NAME, "PERSON")
        amap.add_entity(OTHER_NAME, "PERSON")
        await document_anon_store.set_document_map(doc, amap)
        env.answer = "[PERSON_1] and [PERSON_2]"

        answer = _answer(await env.ask({"message": "Who signed?", "document_ids": [doc]}))

        assert "[PERSON_1]" in env.prompts[-1]
        assert answer == f"[PERSON_1] and {OTHER_NAME}"
        assert KNOWN_NAME not in answer

    def test_multi_document_instruction_uses_neutral_header(self) -> None:
        from septum_api.services.prompts import PromptCatalog

        prompt = PromptCatalog.chat_user_prompt(
            language="en",
            regulations_str="GDPR",
            sanitized_query="q",
            context_text="c",
            has_context=True,
            schema_instruction="",
            placeholder_list_str="",
            output_instruction="",
        )

        assert "--- Document N ---" in prompt
        assert "<filename>" not in prompt
        assert PromptCatalog.document_context_header(2).startswith("--- Document 2 ---")


class TestPreApprovedRetryNumbering:
    async def test_retry_rebuilds_the_unified_space(self, env: _Env) -> None:
        first_name, second_name = "Alice Smith", "Bob Jones"
        doc_a = await env.add_document(["[PERSON_1] owns the house."])
        doc_b = await env.add_document(["[PERSON_1] owns the car."])
        for doc, person in ((doc_a, first_name), (doc_b, second_name)):
            amap = AnonymizationMap(document_id=doc, language="en")
            amap.add_entity(person, "PERSON")
            await document_anon_store.set_document_map(doc, amap)

        await env.set_require_approval(True)
        events = await env.ask({"message": "Who owns what?", "document_ids": [doc_a, doc_b]})
        token = next(e for e in events if e["type"] == "approval_required")["numbering_token"]
        offered = list(env.gate.created[-1]["masked_chunks"])
        assert "[PERSON_2] owns the car." in offered[1]

        env.answer = "[PERSON_1]; [PERSON_2]; [PERSON_3]"
        answer = _answer(
            await env.ask(
                {
                    "message": "Who owns what?",
                    "document_ids": [doc_a, doc_b],
                    "pre_approved_chunks": [{"text": t} for t in offered]
                    + [{"text": f"Paid by {NAME}."}],
                    "numbering_token": token,
                }
            )
        )

        _assert_masked(env.prompts[-1])
        assert answer == f"{first_name}; {second_name}; {NAME}"

    async def test_auto_mode_retry_rebuilds_the_unified_space(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first_name, second_name = "Alice Smith", "Bob Jones"
        doc_a = await env.add_document(["[PERSON_1] owns the house."])
        doc_b = await env.add_document(["[PERSON_1] owns the car."])
        for doc, person in ((doc_a, first_name), (doc_b, second_name)):
            amap = AnonymizationMap(document_id=doc, language="en")
            amap.add_entity(person, "PERSON")
            await document_anon_store.set_document_map(doc, amap)

        async def _retrieve_all(db, query, top_k, documents, relevance_threshold):
            ids = [d.id for d in documents]
            rows = await db.execute(select(Chunk).where(Chunk.document_id.in_(ids)))
            chunks = sorted(rows.scalars().all(), key=lambda c: c.document_id)
            return chunks, sorted({c.document_id for c in chunks})

        async def _classify(**kwargs):
            return "search"

        monkeypatch.setattr(chat_module, "_retrieve_chunks_all_documents", _retrieve_all)
        monkeypatch.setattr(chat_module, "_classify_query_intent", _classify)

        await env.set_require_approval(True)
        events = await env.ask({"message": "Who owns what?"})
        approval = next(e for e in events if e["type"] == "approval_required")
        offered = approval["chunks"]
        assert {c["document_id"] for c in offered} == {doc_a, doc_b}
        assert any("[PERSON_2] owns the car." in c["text"] for c in offered)

        env.answer = "[PERSON_1]; [PERSON_2]"
        answer = _answer(
            await env.ask(
                {
                    "message": "Who owns what?",
                    "pre_approved_chunks": [
                        {"text": c["text"], "document_id": c["document_id"]} for c in offered
                    ],
                    "numbering_token": approval["numbering_token"],
                }
            )
        )

        assert answer == f"{first_name}; {second_name}"


class TestMapPersistence:
    async def _chunk_id(self, env: _Env, doc: int) -> int:
        async with env.session_maker() as session:
            return (
                await session.execute(select(Chunk.id).where(Chunk.document_id == doc))
            ).scalar_one()

    async def test_map_write_failure_aborts_chunk_patch(
        self, env: _Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original_text = "[PERSON_NAME_1] signed the contract."
        doc = await env.add_document([original_text])
        known = await _store_known_map(doc)
        chunk_id = await self._chunk_id(env, doc)
        monkeypatch.setattr(
            document_anon_store, "_map_path", lambda _id: tmp_path / "missing" / "dir" / "m.enc"
        )

        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/chunks/{chunk_id}", json={"sanitized_text": f"Payee {NAME}."}
            )

        assert resp.status_code == 500
        _assert_masked(resp.text)
        async with env.session_maker() as session:
            stored = (
                await session.execute(select(Chunk.sanitized_text).where(Chunk.id == chunk_id))
            ).scalar_one()
        assert stored == original_text
        assert document_anon_store._document_maps[doc] is known
        assert NAME not in known.entity_map

    async def test_edit_reads_map_another_worker_persisted(self, env: _Env) -> None:
        doc = await env.add_document(["[PERSON_1] signed the contract."])
        stale = AnonymizationMap(document_id=doc, language="en")
        stale.add_entity(KNOWN_NAME, "PERSON")
        await document_anon_store.set_document_map(doc, stale)
        newer = copy.deepcopy(stale)
        newer.add_entity(OTHER_NAME, "PERSON")
        await document_anon_store.set_document_map(doc, newer)
        document_anon_store._document_maps[doc] = stale
        chunk_id = await self._chunk_id(env, doc)

        resp = await env.client.patch(
            f"/api/chunks/{chunk_id}", json={"sanitized_text": f"Payee {NAME}."}
        )

        assert resp.status_code == 200
        assert resp.json()["sanitized_text"] == "Payee [PERSON_3]."
        document_anon_store._document_maps.clear()
        reloaded = await document_anon_store.get_document_map(doc)
        assert reloaded.entity_map[OTHER_NAME] == "[PERSON_2]"
        assert reloaded.entity_map[NAME] == "[PERSON_3]"


async def _two_person_documents(env: _Env) -> tuple[int, int]:
    doc_a = await env.add_document(["[PERSON_1] owns the house."])
    doc_b = await env.add_document(["[PERSON_1] owns the car."])
    for doc, person in ((doc_a, "Alice Smith"), (doc_b, "Bob Jones")):
        amap = AnonymizationMap(document_id=doc, language="en")
        amap.add_entity(person, "PERSON")
        await document_anon_store.set_document_map(doc, amap)
    return doc_a, doc_b


class TestRetryNumberingToken:
    """A retry is refused unless it rebuilds the numbering it was offered under."""

    async def _offer(self, env: _Env, doc_ids: list[int]) -> tuple[list[str], str]:
        await env.set_require_approval(True)
        events = await env.ask({"message": "Who owns what?", "document_ids": doc_ids})
        approval = next(e for e in events if e["type"] == "approval_required")
        return [c["text"] for c in approval["chunks"]], approval["numbering_token"]

    async def _retry(
        self, env: _Env, doc_ids: list[int], offered: list[str], token: Optional[str]
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "message": "Who owns what?",
            "document_ids": doc_ids,
            "pre_approved_chunks": [{"text": t} for t in offered],
        }
        if token is not None:
            payload["numbering_token"] = token
        return await env.ask(payload)

    def _assert_refused(self, env: _Env, events: list[dict[str, Any]], sent_before: int) -> None:
        errors = [e for e in events if e["type"] == "error"]
        assert [e.get("code") for e in errors] == ["retry_numbering_changed"]
        assert not [e for e in events if e["type"] in ("answer_chunk", "approval_required")]
        assert len(env.prompts) == sent_before

    async def test_unchanged_retry_restores_the_right_names(self, env: _Env) -> None:
        doc_a, doc_b = await _two_person_documents(env)
        offered, token = await self._offer(env, [doc_a, doc_b])
        env.answer = "[PERSON_1]; [PERSON_2]"

        answer = _answer(await self._retry(env, [doc_a, doc_b], offered, token))

        assert answer == "Alice Smith; Bob Jones"

    async def test_edited_lower_id_map_refuses_the_retry(self, env: _Env) -> None:
        doc_a, doc_b = await _two_person_documents(env)
        offered, token = await self._offer(env, [doc_a, doc_b])
        grown = copy.deepcopy(await document_anon_store.get_document_map(doc_a))
        grown.add_entity("Carol White", "PERSON")
        await document_anon_store.set_document_map(doc_a, grown)
        sent_before = len(env.prompts)
        created_before = len(env.gate.created)

        events = await self._retry(env, [doc_a, doc_b], offered, token)

        self._assert_refused(env, events, sent_before)
        assert len(env.gate.created) == created_before

    async def test_changed_selection_refuses_the_retry(self, env: _Env) -> None:
        doc_a, doc_b = await _two_person_documents(env)
        offered, token = await self._offer(env, [doc_a, doc_b])
        sent_before = len(env.prompts)

        events = await self._retry(env, [doc_b], offered, token)

        self._assert_refused(env, events, sent_before)

    async def test_missing_or_tampered_token_refuses_the_retry(self, env: _Env) -> None:
        doc_a, doc_b = await _two_person_documents(env)
        offered, token = await self._offer(env, [doc_a, doc_b])
        sent_before = len(env.prompts)
        flipped = token[:-1] + ("A" if token[-1] != "A" else "B")

        for bad in (None, "", flipped, "v1.forged"):
            events = await self._retry(env, [doc_a, doc_b], offered, bad)
            self._assert_refused(env, events, sent_before)

    async def test_another_users_token_is_refused(self, env: _Env) -> None:
        doc_a, doc_b = await _two_person_documents(env)
        offered, token = await self._offer(env, [doc_a, doc_b])
        async with env.session_maker() as session:
            admin = User(email="admin@example.com", hashed_password="x", role="admin")
            session.add(admin)
            await session.commit()
        env.user = admin
        sent_before = len(env.prompts)

        events = await self._retry(env, [doc_a, doc_b], offered, token)

        self._assert_refused(env, events, sent_before)


class TestTypedQueryPlaceholder:
    async def test_typed_placeholder_follows_the_unified_numbering(self, env: _Env) -> None:
        await env.set_require_approval(False)
        doc_a = await env.add_document(["[PERSON_1] owns the house."])
        doc_b = await env.add_document(["[PERSON_2] owns the car. [PERSON_1] rents it."])
        amap_a = AnonymizationMap(document_id=doc_a, language="en")
        amap_a.add_entity("Alice Smith", "PERSON")
        await document_anon_store.set_document_map(doc_a, amap_a)
        amap_b = AnonymizationMap(document_id=doc_b, language="en")
        amap_b.entity_map = {"Bob Jones": "[PERSON_2]", "Dan Brown": "[PERSON_1]"}
        await document_anon_store.set_document_map(doc_b, amap_b)

        answer = _answer(
            await env.ask(
                {"message": "Who is [PERSON_2]?", "document_ids": [doc_b, doc_a]}
            )
        )

        prompt = env.prompts[-1]
        assert "Who is [PERSON_1]?" in prompt
        assert answer == "Bob Jones"


class TestApprovalPreviewGate:
    async def test_second_preview_within_the_interval_is_refused(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from septum_api.routers import approval as approval_module
        from septum_api.services.approval_gate import ApprovalGate

        gate = ApprovalGate()
        monkeypatch.setattr(approval_module, "_get_gate", lambda: gate)
        monkeypatch.setattr(approval_module, "PREVIEW_MIN_INTERVAL_SECONDS", 60.0)
        await gate.create(
            session_id="preview-rate",
            masked_prompt="q",
            masked_chunks=["[PERSON_NAME_1] signed."],
            entity_count=0,
            owner_user_id=env.user.id,
            assembly_context={"sanitized_query": "q", "language": "en"},
        )
        body = {"chunks": [{"text": "[PERSON_NAME_1] signed."}]}

        first = await env.client.post("/api/approval/preview-rate/preview-prompt", json=body)
        second = await env.client.post("/api/approval/preview-rate/preview-prompt", json=body)

        assert first.status_code == 200, first.text
        assert second.status_code == 429

    async def test_preview_gates_edits_like_the_send_path(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from septum_api.routers import approval as approval_module
        from septum_api.services.approval_gate import ApprovalGate
        from septum_api.utils.db_helpers import load_settings

        await env.set_require_approval(True)
        gate = ApprovalGate()
        monkeypatch.setattr(approval_module, "_get_gate", lambda: gate)
        monkeypatch.setattr(approval_module, "PREVIEW_MIN_INTERVAL_SECONDS", 0.0)
        amap = AnonymizationMap(document_id=0, language="en")
        amap.add_entity(KNOWN_NAME, "PERSON_NAME")
        offered = "[PERSON_NAME_1] signed the contract."
        await gate.create(
            session_id="preview-1",
            masked_prompt="Who signed?",
            masked_chunks=[offered],
            entity_count=0,
            owner_user_id=env.user.id,
            anon_map=amap,
            assembly_context={
                "sanitized_query": "Who signed?",
                "regulation_names": [],
                "language": "en",
                "output_mode": "chat",
                "column_descriptions": [],
                "query_has_placeholder": False,
            },
        )
        edited = f"{offered} Paid by {NAME} via IBAN {IBAN}, witnessed by {KNOWN_NAME}."

        first = await env.client.post(
            "/api/approval/preview-1/preview-prompt", json={"chunks": [{"text": edited}]}
        )
        second = await env.client.post(
            "/api/approval/preview-1/preview-prompt", json={"chunks": [{"text": edited}]}
        )

        assert first.status_code == 200, first.text
        preview = first.json()["assembled_prompt"]
        _assert_masked(preview)
        assert KNOWN_NAME not in preview
        assert second.json()["assembled_prompt"] == preview
        async with env.session_maker() as session:
            [sent] = await chat_module._gate_edited_chunks(
                [offered], [edited], copy.deepcopy(amap), session, await load_settings(session)
            )
        assert sent in preview
        _, _, session_map = await gate.get_preview_state("preview-1")
        assert session_map.entity_map == amap.entity_map


class _LostLock:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def acquire(self) -> bool:
        self._events.append("acquire")
        return True

    async def extend(self, additional_time: float, replace_ttl: bool = False) -> bool:
        from redis.exceptions import LockNotOwnedError

        self._events.append("extend")
        raise LockNotOwnedError("expired")

    async def release(self) -> None:
        from redis.exceptions import LockNotOwnedError

        self._events.append("release")
        raise LockNotOwnedError("expired")


class _LostLockRedis:
    def __init__(self) -> None:
        self.events: list[str] = []

    def lock(self, name: str, **kwargs: Any) -> _LostLock:
        return _LostLock(self.events)


class TestMapLockLoss:
    async def test_lost_lock_aborts_the_edit_and_writes_nothing(
        self, env: _Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original_text = "[PERSON_NAME_1] signed the contract."
        doc = await env.add_document([original_text])
        await _store_known_map(doc)
        map_file = tmp_path / "maps" / f"{doc}.enc"
        before = map_file.read_bytes()
        fake = _LostLockRedis()

        async def _get_redis():
            return fake

        real_create = pii_gate.create_sanitizer

        async def _tracking_create(db, settings, enable_ollama=False):
            fake.events.append("sanitizer")
            return await real_create(db, settings, enable_ollama=enable_ollama)

        monkeypatch.setattr(document_anon_store, "get_redis", _get_redis)
        monkeypatch.setattr(pii_gate, "create_sanitizer", _tracking_create)
        async with env.session_maker() as session:
            chunk_id = (
                await session.execute(select(Chunk.id).where(Chunk.document_id == doc))
            ).scalar_one()

        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/chunks/{chunk_id}", json={"sanitized_text": f"Payee {NAME}."}
            )

        assert resp.status_code == 500
        _assert_masked(resp.text)
        assert fake.events == ["sanitizer", "acquire", "extend", "release"]
        assert map_file.read_bytes() == before
        async with env.session_maker() as session:
            stored = (
                await session.execute(select(Chunk.sanitized_text).where(Chunk.id == chunk_id))
            ).scalar_one()
        assert stored == original_text


class _NoIndex:
    def delete_index(self, document_id: int) -> None:
        return None


class _FailingIndex:
    def delete_index(self, document_id: int) -> None:
        raise PermissionError(13, "Permission denied", f"/secret/{KNOWN_NAME}.enc")


class TestDocumentDeleteErasure:
    @pytest.fixture(autouse=True)
    def _stub_indexes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from septum_api.services import document_pipeline as pipeline_module

        monkeypatch.setattr(pipeline_module, "VectorStore", _NoIndex)
        monkeypatch.setattr(pipeline_module, "BM25Retriever", _NoIndex)

    async def _set_source(self, env: _Env, doc: int, path: Path) -> None:
        async with env.session_maker() as session:
            stored = await session.get(Document, doc)
            stored.encrypted_path = str(path)
            await session.commit()

    async def _assert_kept(self, env: _Env, doc: int) -> None:
        async with env.session_maker() as session:
            assert await session.get(Document, doc) is not None

    async def test_map_erasure_failure_fails_the_delete(
        self, env: _Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        doc = await env.add_document(["[PERSON_NAME_1] signed."])
        await _store_known_map(doc)
        undeletable = tmp_path / "undeletable"
        undeletable.mkdir()
        monkeypatch.setattr(document_anon_store, "_map_path", lambda _id: undeletable)

        resp = await env.client.delete(f"/api/documents/{doc}")

        assert resp.status_code == 500
        assert "not deleted" in resp.text
        await self._assert_kept(env, doc)

    async def test_missing_source_file_is_already_erased(
        self, env: _Env, tmp_path: Path
    ) -> None:
        doc = await env.add_document(["[PERSON_NAME_1] signed."])
        await self._set_source(env, doc, tmp_path / "gone.enc")

        resp = await env.client.delete(f"/api/documents/{doc}")

        assert resp.status_code == 204
        async with env.session_maker() as session:
            assert await session.get(Document, doc) is None

    async def test_source_file_failure_keeps_record_and_retry_succeeds(
        self, env: _Env, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        doc = await env.add_document(["[PERSON_NAME_1] signed."])
        blocker = tmp_path / f"{KNOWN_NAME}.enc"
        blocker.mkdir()
        await self._set_source(env, doc, blocker)
        await _store_known_map(doc)

        with caplog.at_level("ERROR"):
            resp = await env.client.delete(f"/api/documents/{doc}")

        assert resp.status_code == 500
        assert "not deleted" in resp.text
        await self._assert_kept(env, doc)
        assert f"document_id={doc}" in caplog.text
        assert "tier=source_file" in caplog.text
        assert KNOWN_NAME not in caplog.text
        assert str(tmp_path) not in caplog.text

        blocker.rmdir()
        blocker.write_bytes(b"ciphertext")
        retry = await env.client.delete(f"/api/documents/{doc}")

        assert retry.status_code == 204
        assert not blocker.exists()
        assert await document_anon_store.get_document_map(doc) is None
        async with env.session_maker() as session:
            assert await session.get(Document, doc) is None

    @pytest.mark.parametrize(
        ("attr", "tier"),
        [("VectorStore", "vector_index"), ("BM25Retriever", "bm25_index")],
    )
    async def test_index_failure_keeps_record(
        self,
        env: _Env,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        attr: str,
        tier: str,
    ) -> None:
        from septum_api.services import document_pipeline as pipeline_module

        doc = await env.add_document(["[PERSON_NAME_1] signed."])
        await self._set_source(env, doc, tmp_path / "gone.enc")
        await _store_known_map(doc)
        monkeypatch.setattr(pipeline_module, attr, _FailingIndex)

        with caplog.at_level("ERROR"):
            resp = await env.client.delete(f"/api/documents/{doc}")

        assert resp.status_code == 500
        assert "not deleted" in resp.text
        await self._assert_kept(env, doc)
        assert f"tier={tier}" in caplog.text
        assert KNOWN_NAME not in caplog.text
        assert "/secret/" not in caplog.text
