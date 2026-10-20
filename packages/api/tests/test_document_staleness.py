from __future__ import annotations

"""Per-document policy snapshot, read-time staleness and chat re-masking.

A document records the entity types of the policy its chunks were masked
under. The documents API flags it when the current policy names a type the
snapshot lacks (or the snapshot is missing), and chat re-masks chunks of
such documents with the current policy before they leave the machine. The
cloud LLM call is mocked at ``LLMRouter.complete``; the sanitizer is a
deterministic double so no NER model is loaded.
"""

import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import pytest
from httpx import ASGITransport, AsyncClient
from septum_api.database import (
    _builtin_regulations,
    _seed_defaults,
    build_default_app_settings,
    get_db,
)
from septum_api.main import app
from septum_api.models import Base
from septum_api.models.document import Chunk, Document
from septum_api.models.regulation import RegulationRuleset
from septum_api.models.user import User
from septum_api.routers import chat as chat_module
from septum_api.services import document_anon_store, pii_gate
from septum_api.services import document_pipeline as dp
from septum_api.services.anonymization_map import AnonymizationMap
from septum_api.services.approval_gate import ApprovalChunk, ApprovalDecision
from septum_api.services.llm_router import LLMRouter
from septum_api.services.policy_snapshot import build_policy_snapshot
from septum_api.services.remask_cache import remask_cache
from septum_api.utils.auth_dependency import get_current_user
from septum_core.spans import ResolvedSpan, SanitizeResult
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

DATE_VALUE = "2024-03-15"
KNOWN_NAME = "Jane Roe"
STORED_CHUNK = f"[PERSON_NAME_1] visited the clinic on {DATE_VALUE}."
_PLACEHOLDER_RE = re.compile(r"\[[A-Z][A-Z_]*_\d+\]")


def _hipaa() -> RegulationRuleset:
    return next(r for r in _builtin_regulations() if r.id == "hipaa")


def _hipaa_types() -> list[str]:
    return sorted(_hipaa().entity_types)


def _current_digest() -> str:
    """Digest of the fixture policy: current detector version, no custom recognizer."""
    return build_policy_snapshot([], []).digest


_UNSET: Any = object()


class _DateSanitizer:
    """Sanitizer double: finds ``DATE_VALUE`` and, wrongly, a placeholder's body.

    Flagging ``PERSON_NAME_1`` inside ``[PERSON_NAME_1]`` stands in for a
    recognizer that matches inside an existing placeholder; the gate must
    leave known placeholders intact.
    """

    def __init__(self) -> None:
        self.texts: list[str] = []

    def sanitize(self, text: str, language: str, anon_map: Any) -> SanitizeResult:
        self.texts.append(text)
        spans: list[ResolvedSpan] = []
        for value, entity_type in ((DATE_VALUE, "DATE"), ("PERSON_NAME_1", "PERSON_NAME")):
            start = text.find(value)
            while start != -1:
                spans.append(ResolvedSpan(start, start + len(value), entity_type, "", 1.0))
                start = text.find(value, start + 1)
        return SanitizeResult(sanitized_text=text, entity_count=len(spans), detected_spans=spans)


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
    def __init__(self, session_maker: Any, engine: Any, client: AsyncClient, user: User) -> None:
        self.session_maker = session_maker
        self.engine = engine
        self.client = client
        self.user = user
        self.prompts: list[str] = []
        self.sanitizer = _DateSanitizer()
        self.gate = _ApprovingGate()
        self.sanitizer_kwargs: list[dict[str, Any]] = []

    async def add_document(
        self,
        snapshot: Optional[list[str]],
        status: str = "completed",
        chunk_texts: tuple[str, ...] = (),
        digest: Optional[str] = _UNSET,
    ) -> int:
        if digest is _UNSET:
            digest = _current_digest() if snapshot is not None else None
        async with self.session_maker() as session:
            doc = Document(
                filename="stored.pdf",
                original_filename="visit.pdf",
                file_type="application/pdf",
                file_format="pdf",
                detected_language="en",
                encrypted_path="/nonexistent",
                file_size_bytes=10,
                ocr_confidence=0.0,
                active_regulation_ids=["hipaa"],
                sanitized_entity_types=snapshot,
                sanitized_policy_digest=digest,
                ingestion_status=status,
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

    async def set_hipaa_active(self, active: bool) -> None:
        async with self.session_maker() as session:
            await session.execute(
                update(RegulationRuleset)
                .where(RegulationRuleset.id == "hipaa")
                .values(is_active=active)
            )
            await session.commit()

    async def get_doc(self, document_id: int) -> dict[str, Any]:
        resp = await self.client.get(f"/api/documents/{document_id}")
        assert resp.status_code == 200, resp.text
        return resp.json()

    async def set_require_approval(self, value: bool) -> None:
        async with self.session_maker() as session:
            settings = (await session.execute(select(type(build_default_app_settings())))).scalars().first()
            settings.require_approval = value
            await session.commit()

    async def events(self, document_id: int, **extra: Any) -> list[dict[str, Any]]:
        resp = await self.client.post(
            "/api/chat/ask",
            json={"message": "When was the visit?", "document_ids": [document_id], **extra},
        )
        assert resp.status_code == 200, resp.text
        return [
            json.loads(line[len("data: "):])
            for line in resp.text.splitlines()
            if line.startswith("data: ")
        ]

    async def ask(self, document_id: int, **extra: Any) -> str:
        events = await self.events(document_id, **extra)
        assert not [e for e in events if e["type"] == "error"], events
        return "".join(e["text"] for e in events if e["type"] == "answer_chunk")


@pytest.fixture
async def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Env]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stale.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _override_get_db():
        async with session_maker() as session:
            yield session

    async with session_maker() as session:
        user = User(email="owner@example.com", hashed_password="x", role="editor")
        session.add(user)
        hipaa = _hipaa()
        hipaa.is_active = True
        session.add(hipaa)
        settings = build_default_app_settings()
        settings.require_approval = False
        session.add(settings)
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        env = _Env(session_maker, engine, client, user)

        async def _create_sanitizer(db, settings, enable_ollama=False, policy=None):
            env.sanitizer_kwargs.append({"enable_ollama": enable_ollama})
            return env.sanitizer

        async def _retrieve(db, document_id, query, top_k, chunk_count, relevance_threshold):
            rows = await db.execute(select(Chunk).where(Chunk.document_id == document_id))
            return list(rows.scalars().all())

        async def _complete(self, messages, **kwargs):
            prompt = messages[-1]["content"]
            env.prompts.append(prompt)
            return "Seen: " + " ".join(sorted(set(_PLACEHOLDER_RE.findall(prompt))))

        monkeypatch.setattr(document_anon_store, "_ANON_MAP_DIR", tmp_path / "maps")
        monkeypatch.setattr(document_anon_store, "_document_maps", {})
        remask_cache.clear()
        monkeypatch.setattr(chat_module, "create_sanitizer", _create_sanitizer)
        monkeypatch.setattr(pii_gate, "create_sanitizer", _create_sanitizer)
        monkeypatch.setattr(chat_module, "_retrieve_chunks", _retrieve)
        monkeypatch.setattr(LLMRouter, "complete", _complete)
        monkeypatch.setattr(chat_module, "get_approval_gate", lambda: env.gate)

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user] = lambda: env.user
        try:
            yield env
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)
            await engine.dispose()


class TestSnapshot:
    async def test_pipeline_records_hipaa_types_including_date(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Lock:
            async def ensure_held(self) -> None:
                return None

        @asynccontextmanager
        async def _map_lock(document_id):
            yield _Lock()

        async def _noop(*args, **kwargs):
            return None

        async def _no_chunks(self, file_format, text):
            return []

        async def _persist(self, db, document_id, chunks, raw_texts):
            return []

        monkeypatch.setattr(dp, "create_sanitizer", _noop)
        monkeypatch.setattr(dp.DocumentPipeline, "_build_chunks", _no_chunks)
        monkeypatch.setattr(dp.DocumentPipeline, "_persist_chunks", _persist)
        monkeypatch.setattr(dp, "document_map_lock", _map_lock)
        monkeypatch.setattr(dp, "set_document_map", _noop)
        monkeypatch.setattr(dp, "replace_index_for_document", _noop)
        monkeypatch.setattr(dp, "recompute_relationships_for_document", _noop)
        doc_id = await env.add_document(None, status="processing")

        async with env.session_maker() as session:
            doc = await session.get(Document, doc_id)
            await dp.DocumentPipeline(settings=build_default_app_settings()).run(
                db=session,
                document=doc,
                file_format="pdf",
                ingested_text="",
                ingestion_confidence=None,
            )

        async with env.session_maker() as session:
            stored = await session.get(Document, doc_id)
        assert stored.sanitized_entity_types == _hipaa_types()
        assert "DATE" in stored.sanitized_entity_types
        assert stored.sanitized_policy_digest == _current_digest()


class TestStalenessFields:
    async def test_full_snapshot_is_fresh(self, env: _Env) -> None:
        doc = await env.get_doc(await env.add_document(_hipaa_types()))

        assert doc["needs_reprocessing"] is False
        assert doc["missing_entity_types"] == []
        assert doc["sanitized_policy_known"] is True

    async def test_snapshot_without_date_is_stale(self, env: _Env) -> None:
        snapshot = [t for t in _hipaa_types() if t != "DATE"]
        doc = await env.get_doc(await env.add_document(snapshot))

        assert doc["needs_reprocessing"] is True
        assert doc["missing_entity_types"] == ["DATE"]
        assert doc["sanitized_policy_known"] is True

    async def test_deactivated_regulation_does_not_flag(self, env: _Env) -> None:
        doc_id = await env.add_document([t for t in _hipaa_types() if t != "DATE"])
        await env.set_hipaa_active(False)

        doc = await env.get_doc(doc_id)

        assert doc["needs_reprocessing"] is False
        assert doc["missing_entity_types"] == []

    async def test_extra_snapshot_types_do_not_flag(self, env: _Env) -> None:
        doc = await env.get_doc(await env.add_document(_hipaa_types() + ["EMPLOYEE_ID"]))

        assert doc["needs_reprocessing"] is False

    async def test_missing_snapshot_is_stale_and_lists_no_types(self, env: _Env) -> None:
        doc = await env.get_doc(await env.add_document(None))

        assert doc["needs_reprocessing"] is True
        assert doc["missing_entity_types"] == []
        assert doc["sanitized_policy_known"] is False

    async def test_types_without_digest_is_an_unknown_policy(self, env: _Env) -> None:
        doc = await env.get_doc(await env.add_document(_hipaa_types(), digest=None))

        assert doc["needs_reprocessing"] is True
        assert doc["missing_entity_types"] == []
        assert doc["sanitized_policy_known"] is False

    async def test_other_digest_with_every_type_is_stale(self, env: _Env) -> None:
        doc = await env.get_doc(await env.add_document(_hipaa_types(), digest="0" * 64))

        assert doc["needs_reprocessing"] is True
        assert doc["missing_entity_types"] == []
        assert doc["sanitized_policy_known"] is True

    async def test_new_detector_version_flags_documents(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from septum_api.services import policy_snapshot

        doc_id = await env.add_document(_hipaa_types())
        monkeypatch.setattr(policy_snapshot, "DETECTOR_VERSION", "next")

        assert (await env.get_doc(doc_id))["needs_reprocessing"] is True

    async def test_removed_non_pii_rule_flags_documents(self, env: _Env) -> None:
        from septum_api.models.regulation import NonPiiRule

        async with env.session_maker() as session:
            rule = NonPiiRule(
                pattern_type="token", pattern="monday", languages=["en"],
                entity_types=["DATE"], min_score=None, is_active=True,
            )
            session.add(rule)
            await session.commit()
        snapshot = build_policy_snapshot(
            [_hipaa().entity_types], [], [("token", "monday", ["en"], ["DATE"], None)]
        )
        doc_id = await env.add_document(snapshot.entity_types, digest=snapshot.digest)
        assert (await env.get_doc(doc_id))["needs_reprocessing"] is False

        async with env.session_maker() as session:
            stored = await session.get(NonPiiRule, rule.id)
            stored.is_active = False
            await session.commit()

        doc = await env.get_doc(doc_id)
        assert doc["needs_reprocessing"] is True
        assert doc["missing_entity_types"] == []
        assert doc["sanitized_policy_known"] is True

    async def test_edited_custom_recognizer_flags_documents(self, env: _Env) -> None:
        from septum_api.models.regulation import CustomRecognizer

        async with env.session_maker() as session:
            rec = CustomRecognizer(
                name="emp", entity_type="EMPLOYEE_ID", detection_method="regex",
                pattern=r"E\d{4}", placeholder_label="EMPLOYEE_ID", is_active=True,
            )
            session.add(rec)
            await session.commit()
            definition = [("EMPLOYEE_ID", "regex", r"E\d{4}", None, None, [])]
        snapshot = build_policy_snapshot([_hipaa().entity_types], definition)
        doc_id = await env.add_document(snapshot.entity_types, digest=snapshot.digest)
        assert (await env.get_doc(doc_id))["needs_reprocessing"] is False

        async with env.session_maker() as session:
            stored = await session.get(CustomRecognizer, rec.id)
            stored.pattern = r"E\d{6}"
            await session.commit()

        doc = await env.get_doc(doc_id)
        assert doc["needs_reprocessing"] is True
        assert doc["missing_entity_types"] == []

    @pytest.mark.parametrize("status", ["processing", "failed"])
    async def test_unfinished_documents_are_never_flagged(self, env: _Env, status: str) -> None:
        doc = await env.get_doc(await env.add_document(None, status=status))

        assert doc["needs_reprocessing"] is False
        assert doc["missing_entity_types"] == []

    async def test_list_flags_each_document(self, env: _Env) -> None:
        fresh = await env.add_document(_hipaa_types())
        stale = await env.add_document([t for t in _hipaa_types() if t != "DATE"])

        resp = await env.client.get("/api/documents")

        assert resp.status_code == 200
        flags = {d["id"]: d["needs_reprocessing"] for d in resp.json()["items"]}
        assert flags == {fresh: False, stale: True}

    async def test_list_query_count_does_not_grow_with_documents(self, env: _Env) -> None:
        statements: list[str] = []

        def _count(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        async def _list_queries() -> int:
            statements.clear()
            resp = await env.client.get("/api/documents")
            assert resp.status_code == 200
            return len(statements)

        event.listen(env.engine.sync_engine, "before_cursor_execute", _count)
        try:
            await env.add_document(None)
            one = await _list_queries()
            for _ in range(4):
                await env.add_document([t for t in _hipaa_types() if t != "DATE"])
            five = await _list_queries()
        finally:
            event.remove(env.engine.sync_engine, "before_cursor_execute", _count)

        assert five == one


class TestChatRemasking:
    async def _store_map(self, document_id: int) -> None:
        amap = AnonymizationMap(document_id=document_id, language="en")
        amap.add_entity(KNOWN_NAME, "PERSON_NAME")
        await document_anon_store.set_document_map(document_id, amap)

    async def test_stale_chunk_date_reaches_prompt_as_placeholder(self, env: _Env) -> None:
        doc_id = await env.add_document(
            [t for t in _hipaa_types() if t != "DATE"], chunk_texts=(STORED_CHUNK,)
        )
        await self._store_map(doc_id)

        answer = await env.ask(doc_id)

        prompt = env.prompts[-1]
        assert DATE_VALUE not in prompt
        assert KNOWN_NAME not in prompt
        assert re.search(r"visited the clinic on \[DATE_\d+\]", prompt)
        assert "[PERSON_NAME_1] visited" in prompt
        assert "[[" not in prompt and "]]" not in prompt
        assert DATE_VALUE in answer
        assert KNOWN_NAME in answer

    async def test_new_entities_join_the_document_map(self, env: _Env) -> None:
        doc_id = await env.add_document(None, chunk_texts=(STORED_CHUNK,))
        await self._store_map(doc_id)

        await env.ask(doc_id)
        first_prompt = env.prompts[-1]
        await env.ask(doc_id)

        stored = await document_anon_store.load_persisted_document_map(doc_id)
        assert stored is not None
        assert stored.entity_map[DATE_VALUE].startswith("[DATE_")
        assert stored.entity_map[KNOWN_NAME] == "[PERSON_NAME_1]"
        assert env.prompts[-1] == first_prompt

    async def test_fresh_document_chunks_are_not_sanitized_again(self, env: _Env) -> None:
        doc_id = await env.add_document(_hipaa_types(), chunk_texts=(STORED_CHUNK,))
        await self._store_map(doc_id)

        await env.ask(doc_id)

        assert not [t for t in env.sanitizer.texts if "visited the clinic" in t]
        assert STORED_CHUNK in env.prompts[-1]


class TestChatUnfinishedDocuments:
    async def _store_map(self, document_id: int) -> None:
        amap = AnonymizationMap(document_id=document_id, language="en")
        amap.add_entity(KNOWN_NAME, "PERSON_NAME")
        await document_anon_store.set_document_map(document_id, amap)

    @pytest.mark.parametrize("status", ["processing", "failed"])
    @pytest.mark.parametrize("snapshot_full", [True, False])
    async def test_chunks_of_unfinished_documents_are_remasked(
        self, env: _Env, status: str, snapshot_full: bool
    ) -> None:
        doc_id = await env.add_document(
            _hipaa_types() if snapshot_full else None,
            status=status,
            chunk_texts=(STORED_CHUNK,),
        )
        await self._store_map(doc_id)

        await env.ask(doc_id)

        prompt = env.prompts[-1]
        assert DATE_VALUE not in prompt
        assert re.search(r"visited the clinic on \[DATE_\d+\]", prompt)


class TestChatApprovalAndRetry:
    async def _stale_doc(self, env: _Env) -> int:
        doc_id = await env.add_document(
            [t for t in _hipaa_types() if t != "DATE"], chunk_texts=(STORED_CHUNK,)
        )
        amap = AnonymizationMap(document_id=doc_id, language="en")
        amap.add_entity(KNOWN_NAME, "PERSON_NAME")
        await document_anon_store.set_document_map(doc_id, amap)
        return doc_id

    async def test_approval_modal_offers_remasked_stale_chunk(self, env: _Env) -> None:
        doc_id = await self._stale_doc(env)
        await env.set_require_approval(True)

        events = await env.events(doc_id)

        assert [e for e in events if e["type"] == "approval_required"]
        offered = " ".join(env.gate.created[-1]["masked_chunks"])
        assert DATE_VALUE not in offered
        assert re.search(r"on \[DATE_\d+\]", offered)
        assert DATE_VALUE not in env.prompts[-1]

    async def test_approval_edit_reintroducing_the_date_is_remasked(self, env: _Env) -> None:
        doc_id = await self._stale_doc(env)
        await env.set_require_approval(True)
        env.gate.edit = lambda texts: [f"{t} Seen again on {DATE_VALUE}." for t in texts]

        answer = await env.ask(doc_id)

        assert DATE_VALUE not in env.prompts[-1]
        assert DATE_VALUE in answer

    async def test_retry_after_map_settled_remasks_client_text_with_current_policy(
        self, env: _Env
    ) -> None:
        doc_id = await self._stale_doc(env)
        await env.set_require_approval(True)
        await env.events(doc_id)
        events = await env.events(doc_id)
        token = next(e for e in events if e["type"] == "approval_required")["numbering_token"]
        offered = list(env.gate.created[-1]["masked_chunks"])
        resanitized_before = len(env.sanitizer.texts)

        answer = await env.ask(
            doc_id,
            pre_approved_chunks=[
                {"text": t, "document_id": doc_id} for t in offered
            ]
            + [{"text": f"Follow-up on {DATE_VALUE}."}],
            numbering_token=token,
        )

        prompt = env.prompts[-1]
        assert DATE_VALUE not in prompt
        assert KNOWN_NAME not in prompt
        assert len(env.sanitizer.texts) > resanitized_before
        assert DATE_VALUE in answer

    async def test_retry_of_the_turn_that_remasked_is_not_refused(
        self, env: _Env
    ) -> None:
        doc_id = await self._stale_doc(env)
        await env.set_require_approval(True)
        events = await env.events(doc_id)
        token = next(e for e in events if e["type"] == "approval_required")["numbering_token"]
        offered = list(env.gate.created[-1]["masked_chunks"])
        resanitized_before = len(env.sanitizer.texts)

        answer = await env.ask(
            doc_id,
            pre_approved_chunks=[
                {"text": t, "document_id": doc_id} for t in offered
            ]
            + [{"text": f"Follow-up on {DATE_VALUE}."}],
            numbering_token=token,
        )

        prompt = env.prompts[-1]
        assert DATE_VALUE not in prompt
        assert KNOWN_NAME not in prompt
        assert len(env.sanitizer.texts) > resanitized_before
        assert DATE_VALUE in answer


class TestSeedSyncMakesDocumentsStale:
    async def test_startup_sync_adding_date_flags_completed_documents(self, env: _Env) -> None:
        await _seed_defaults(env.session_maker)
        async with env.session_maker() as session:
            hipaa = await session.get(RegulationRuleset, "hipaa")
            hipaa.entity_types = [t for t in hipaa.entity_types if t != "DATE"]
            await session.commit()
        async with env.session_maker() as session:
            from septum_api.services.policy_snapshot import current_policy_snapshot

            old_policy = await current_policy_snapshot(session)
        assert "DATE" not in old_policy.entity_types
        completed = await env.add_document(old_policy.entity_types, digest=old_policy.digest)
        unfinished = await env.add_document(None, status="processing")
        assert (await env.get_doc(completed))["needs_reprocessing"] is False

        await _seed_defaults(env.session_maker)

        flagged = await env.get_doc(completed)
        assert flagged["needs_reprocessing"] is True
        assert flagged["missing_entity_types"] == ["DATE"]
        assert flagged["sanitized_policy_known"] is True
        assert (await env.get_doc(unfinished))["needs_reprocessing"] is False


class TestRemaskCache:
    async def _stale_doc(self, env: _Env) -> int:
        doc_id = await env.add_document(
            [t for t in _hipaa_types() if t != "DATE"], chunk_texts=(STORED_CHUNK,)
        )
        amap = AnonymizationMap(document_id=doc_id, language="en")
        amap.add_entity(KNOWN_NAME, "PERSON_NAME")
        await document_anon_store.set_document_map(doc_id, amap)
        return doc_id

    def _chunk_sanitize_calls(self, env: _Env) -> int:
        return len([t for t in env.sanitizer.texts if "visited the clinic" in t])

    async def test_second_turn_reuses_the_remasked_chunk(self, env: _Env) -> None:
        doc_id = await self._stale_doc(env)

        await env.ask(doc_id)
        first = env.prompts[-1]
        answer = await env.ask(doc_id)

        assert self._chunk_sanitize_calls(env) == 1
        assert env.prompts[-1] == first
        assert DATE_VALUE in answer

    async def test_policy_digest_change_remasks_again(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from septum_api.services import policy_snapshot

        doc_id = await self._stale_doc(env)
        await env.ask(doc_id)
        monkeypatch.setattr(policy_snapshot, "DETECTOR_VERSION", "next")

        await env.ask(doc_id)

        assert self._chunk_sanitize_calls(env) == 2

    async def test_hit_whose_placeholder_the_map_lost_is_dropped(self, env: _Env) -> None:
        doc_id = await self._stale_doc(env)
        await env.ask(doc_id)
        amap = AnonymizationMap(document_id=doc_id, language="en")
        amap.add_entity(KNOWN_NAME, "PERSON_NAME")
        await document_anon_store.set_document_map(doc_id, amap)

        await env.ask(doc_id)

        assert self._chunk_sanitize_calls(env) == 2
        assert DATE_VALUE not in env.prompts[-1]

    def test_lru_evicts_oldest_and_forgets_a_document(self) -> None:
        from septum_api.services.remask_cache import RemaskCache

        cache = RemaskCache(max_entries=2)
        amap = AnonymizationMap(document_id=1, language="en")
        keys = [RemaskCache.key(doc, chunk, "d", "t") for doc, chunk in ((1, 1), (1, 2), (2, 3))]
        for k in keys:
            cache.put(k, "text")

        assert cache.get(keys[0], amap) is None
        assert cache.get(keys[1], amap) == "text"
        cache.forget_document(1)
        assert cache.get(keys[1], amap) is None
        assert cache.get(keys[2], amap) == "text"

    async def test_pipeline_run_forgets_the_document(self, env: _Env) -> None:
        remask_cache.put(remask_cache.key(7, 1, "d", "t"), "text")

        await dp.erase_document_artifacts(7)

        assert len(remask_cache) == 0


class TestMapLockDiscipline:
    async def _stale_doc(self, env: _Env, with_map: bool = True) -> int:
        doc_id = await env.add_document(
            [t for t in _hipaa_types() if t != "DATE"], chunk_texts=(STORED_CHUNK,)
        )
        if with_map:
            amap = AnonymizationMap(document_id=doc_id, language="en")
            amap.add_entity(KNOWN_NAME, "PERSON_NAME")
            await document_anon_store.set_document_map(doc_id, amap)
        return doc_id

    async def test_sanitizer_runs_without_the_map_lock(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        held: list[bool] = [False]
        seen: list[bool] = []
        real_lock = pii_gate.document_map_lock
        real_sanitize = env.sanitizer.sanitize

        @asynccontextmanager
        async def _tracking_lock(document_id):
            async with real_lock(document_id) as lock:
                held[0] = True
                try:
                    yield lock
                finally:
                    held[0] = False

        def _sanitize(text, language, anon_map):
            seen.append(held[0])
            return real_sanitize(text, language, anon_map)

        monkeypatch.setattr(pii_gate, "document_map_lock", _tracking_lock)
        monkeypatch.setattr(env.sanitizer, "sanitize", _sanitize)

        await env.ask(await self._stale_doc(env))

        assert seen and not any(seen)

    async def test_map_erased_mid_turn_aborts_without_rebuilding_it(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        doc_id = await self._stale_doc(env)
        real_gate = pii_gate.gate_untrusted_texts

        async def _gate_then_erase(texts, working, db, settings, **kwargs):
            gated = await real_gate(texts, working, db, settings, **kwargs)
            await document_anon_store.pop_document_map(doc_id)
            return gated

        monkeypatch.setattr(pii_gate, "gate_untrusted_texts", _gate_then_erase)

        events = await env.events(doc_id)

        assert [e for e in events if e["type"] == "error"]
        assert not [e for e in events if e["type"] == "answer_chunk"]
        assert not env.prompts
        assert await document_anon_store.load_persisted_document_map(doc_id) is None

    async def test_outdated_document_without_a_map_aborts_the_turn(self, env: _Env) -> None:
        doc_id = await self._stale_doc(env, with_map=False)

        events = await env.events(doc_id)

        assert [e for e in events if e["type"] == "error"]
        assert not env.prompts
        assert await document_anon_store.load_persisted_document_map(doc_id) is None

    async def test_concurrent_map_change_is_merged_without_reusing_numbers(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        doc_id = await self._stale_doc(env)
        real_gate = pii_gate.gate_untrusted_texts
        calls: list[int] = []

        async def _gate_while_another_worker_writes(texts, working, db, settings, **kwargs):
            gated = await real_gate(texts, working, db, settings, **kwargs)
            calls.append(1)
            if len(calls) == 1:
                other = await document_anon_store.load_persisted_document_map(doc_id)
                other.add_entity("1999-12-31", "DATE")
                await document_anon_store.set_document_map(doc_id, other)
            return gated

        monkeypatch.setattr(pii_gate, "gate_untrusted_texts", _gate_while_another_worker_writes)

        answer = await env.ask(doc_id)

        stored = await document_anon_store.load_persisted_document_map(doc_id)
        assert len(calls) == 2
        assert stored.entity_map["1999-12-31"] != stored.entity_map[DATE_VALUE]
        assert DATE_VALUE not in env.prompts[-1]
        assert DATE_VALUE in answer

    async def test_map_that_keeps_changing_is_refused_with_409(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi import HTTPException

        doc_id = await self._stale_doc(env)
        real_gate = pii_gate.gate_untrusted_texts
        calls: list[int] = []

        async def _gate_while_another_worker_writes(texts, working, db, settings, **kwargs):
            gated = await real_gate(texts, working, db, settings, **kwargs)
            calls.append(1)
            other = await document_anon_store.load_persisted_document_map(doc_id)
            other.add_entity(f"1999-12-{len(calls):02d}", "DATE")
            await document_anon_store.set_document_map(doc_id, other)
            return gated

        monkeypatch.setattr(pii_gate, "gate_untrusted_texts", _gate_while_another_worker_writes)

        async with env.session_maker() as session:
            document = await session.get(Document, doc_id)
            with pytest.raises(HTTPException) as caught:
                await pii_gate.remask_document_texts(
                    session, build_default_app_settings(), document, [STORED_CHUNK], env.sanitizer
                )

        assert caught.value.status_code == 409
        assert len(calls) == pii_gate._MAP_MERGE_ATTEMPTS

    async def test_fresh_map_reserves_indexes_already_in_the_texts(self, env: _Env) -> None:
        doc_id = await env.add_document(None)

        async with env.session_maker() as session:
            document = await session.get(Document, doc_id)
            [gated] = await pii_gate.gate_document_texts(
                session,
                build_default_app_settings(),
                document,
                [f"[DATE_1] and {DATE_VALUE}"],
                sanitizer=env.sanitizer,
            )

        assert gated == "[DATE_1] and [DATE_2]"
        stored = await document_anon_store.load_persisted_document_map(doc_id)
        assert stored.entity_map[DATE_VALUE] == "[DATE_2]"


class TestRemaskConfigAndScope:
    async def test_remask_sanitizer_matches_the_ingestion_sanitizer(self, env: _Env) -> None:
        from septum_api.services.sanitizer_factory import DOCUMENT_SANITIZER_ENABLE_OLLAMA

        doc_id = await env.add_document(
            [t for t in _hipaa_types() if t != "DATE"], chunk_texts=(STORED_CHUNK,)
        )
        amap = AnonymizationMap(document_id=doc_id, language="en")
        await document_anon_store.set_document_map(doc_id, amap)

        await env.ask(doc_id)

        remask_calls = [t for t in env.sanitizer.texts if "visited the clinic" in t]
        assert DOCUMENT_SANITIZER_ENABLE_OLLAMA is False
        assert remask_calls
        assert env.sanitizer_kwargs
        assert all(
            k == {"enable_ollama": DOCUMENT_SANITIZER_ENABLE_OLLAMA}
            for k in env.sanitizer_kwargs
        )

    async def test_chunk_of_a_document_outside_the_turn_is_refused(self, env: _Env) -> None:
        doc_id = await env.add_document(None, chunk_texts=(STORED_CHUNK,))
        async with env.session_maker() as session:
            chunks = list((await session.execute(select(Chunk))).scalars().all())
            await session.get(Document, doc_id)
            with pytest.raises(LookupError):
                await chat_module._resanitize_outdated_chunks(
                    session, build_default_app_settings(), chunks, {}
                )
        assert not env.sanitizer.texts
