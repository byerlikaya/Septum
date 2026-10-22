from __future__ import annotations

"""The chat re-mask cache must not outlive a change of the policy's entity types.

The staleness digest leaves entity types out on purpose, so the cache keys on
the full policy identity. Without that, a chunk re-masked before a regulation
was activated would be served from the cache and the new type's values would
reach the cloud LLM in clear. The LLM call is mocked at ``LLMRouter.complete``;
the sanitizer double detects only the types the current policy names.

A document map that is missing, erased or conflicting mid-turn ends the chat
turn with a typed SSE error code before anything reaches the cloud LLM.
"""

import json
import re
from pathlib import Path
from typing import Any, AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from septum_api.database import build_default_app_settings, get_db
from septum_api.main import app
from septum_api.models import Base
from septum_api.models.document import Chunk, Document
from septum_api.models.regulation import RegulationRuleset
from septum_api.models.user import User
from septum_api.routers import chat as chat_module
from septum_api.services import document_anon_store, pii_gate
from septum_api.services.anonymization_map import AnonymizationMap
from septum_api.services.llm_router import LLMRouter
from septum_api.services.policy_snapshot import (
    build_policy_snapshot,
    current_policy_entity_types,
)
from septum_api.services.remask_cache import remask_cache
from septum_api.utils.auth_dependency import get_current_user
from septum_core.spans import ResolvedSpan, SanitizeResult
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

DATE_VALUE = "2024-03-15"
EMAIL_VALUE = "jane.roe@example.org"
STORED_CHUNK = f"Visit on {DATE_VALUE}, contact {EMAIL_VALUE}."
_VALUES = {"DATE": DATE_VALUE, "EMAIL_ADDRESS": EMAIL_VALUE}
_PLACEHOLDER_RE = re.compile(r"\[[A-Z][A-Z_]*_\d+\]")


class _PolicySanitizer:
    """Sanitizer double that finds the fixture values of the active types only."""

    def __init__(self, entity_types: list[str], log: list[str]) -> None:
        self._types = entity_types
        self._log = log

    def sanitize(self, text: str, language: str, anon_map: Any) -> SanitizeResult:
        self._log.append(text)
        spans = []
        for entity_type in self._types:
            value = _VALUES.get(entity_type)
            start = text.find(value) if value else -1
            if start != -1:
                spans.append(ResolvedSpan(start, start + len(value), entity_type, "", 1.0))
        return SanitizeResult(sanitized_text=text, entity_count=len(spans), detected_spans=spans)


@pytest.fixture
async def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, Any]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cache.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _override_get_db():
        async with session_maker() as session:
            yield session

    async with session_maker() as session:
        user = User(email="owner@example.com", hashed_password="x", role="editor")
        settings = build_default_app_settings()
        settings.require_approval = False
        session.add_all([
            user,
            settings,
            RegulationRuleset(
                id="reg_dates", display_name="Dates", region="X",
                entity_types=["DATE"], is_active=True,
            ),
            RegulationRuleset(
                id="reg_contacts", display_name="Contacts", region="X",
                entity_types=["EMAIL_ADDRESS"], is_active=False,
            ),
        ])
        await session.commit()
        doc = Document(
            filename="stored.pdf", original_filename="visit.pdf",
            file_type="application/pdf", file_format="pdf", detected_language="en",
            encrypted_path="/nonexistent", file_size_bytes=10, ocr_confidence=0.0,
            active_regulation_ids=[], ingestion_status="completed", chunk_count=1,
            sanitized_entity_types=[],
            sanitized_policy_digest=build_policy_snapshot([], []).digest,
            sanitized_language="en",
            user_id=user.id,
        )
        session.add(doc)
        await session.flush()
        session.add(
            Chunk(document_id=doc.id, index=0, sanitized_text=STORED_CHUNK, char_count=len(STORED_CHUNK))
        )
        await session.commit()

    state: dict[str, Any] = {"doc_id": doc.id, "prompts": [], "sanitized": [], "sm": session_maker}

    async def _create_sanitizer(db, settings, enable_ollama=False, policy=None):
        types = policy.entity_types if policy is not None else await current_policy_entity_types(db)
        return _PolicySanitizer(list(types), state["sanitized"])

    async def _retrieve(db, document_id, query, top_k, chunk_count, relevance_threshold):
        rows = await db.execute(select(Chunk).where(Chunk.document_id == document_id))
        return list(rows.scalars().all())

    async def _complete(self, messages, **kwargs):
        prompt = messages[-1]["content"]
        state["prompts"].append(prompt)
        return "Seen: " + " ".join(sorted(set(_PLACEHOLDER_RE.findall(prompt))))

    monkeypatch.setattr(document_anon_store, "_ANON_MAP_DIR", tmp_path / "maps")
    monkeypatch.setattr(document_anon_store, "_document_maps", {})
    remask_cache.clear()
    monkeypatch.setattr(chat_module, "create_sanitizer", _create_sanitizer)
    monkeypatch.setattr(pii_gate, "create_sanitizer", _create_sanitizer)
    monkeypatch.setattr(chat_module, "_retrieve_chunks", _retrieve)
    monkeypatch.setattr(LLMRouter, "complete", _complete)
    await document_anon_store.set_document_map(
        doc.id, AnonymizationMap(document_id=doc.id, language="en")
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        state["client"] = client
        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user] = lambda: user
        try:
            yield state
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)
            remask_cache.clear()
            await engine.dispose()


async def _ask(env: dict[str, Any]) -> str:
    resp = await env["client"].post(
        "/api/chat/ask",
        json={"message": "When was the visit?", "document_ids": [env["doc_id"]]},
    )
    assert resp.status_code == 200, resp.text
    events = [
        json.loads(line[len("data: "):])
        for line in resp.text.splitlines()
        if line.startswith("data: ")
    ]
    assert not [e for e in events if e["type"] == "error"], events
    return "".join(e["text"] for e in events if e["type"] == "answer_chunk")


def _chunk_sanitize_calls(env: dict[str, Any]) -> int:
    return len([t for t in env["sanitized"] if "contact" in t])


async def test_activated_entity_type_bypasses_the_cached_remask(env: dict[str, Any]) -> None:
    await _ask(env)
    first = env["prompts"][-1]
    assert DATE_VALUE not in first
    assert EMAIL_VALUE in first

    async with env["sm"]() as session:
        await session.execute(
            update(RegulationRuleset)
            .where(RegulationRuleset.id == "reg_contacts")
            .values(is_active=True)
        )
        await session.commit()

    answer = await _ask(env)

    prompt = env["prompts"][-1]
    assert _chunk_sanitize_calls(env) == 2
    assert EMAIL_VALUE not in prompt
    assert DATE_VALUE not in prompt
    assert re.search(r"contact \[EMAIL_ADDRESS_\d+\]", prompt)
    assert EMAIL_VALUE in answer


async def test_unchanged_policy_still_uses_the_cache(env: dict[str, Any]) -> None:
    await _ask(env)
    await _ask(env)

    assert _chunk_sanitize_calls(env) == 1


def test_cache_key_changes_with_entity_types_while_the_digest_does_not() -> None:
    before = build_policy_snapshot([["DATE"]], [])
    after = build_policy_snapshot([["DATE"], ["EMAIL_ADDRESS"]], [])

    assert before.digest == after.digest
    assert before.cache_key != after.cache_key


def test_character_budget_evicts_least_recently_used_entries() -> None:
    from septum_api.services.anonymization_map import AnonymizationMap
    from septum_api.services.remask_cache import RemaskCache

    cache = RemaskCache(max_entries=100, max_chars=10)
    amap = AnonymizationMap(document_id=1, language="en")
    first, second, third = (RemaskCache.key(1, i, "p", "t", "en") for i in range(3))
    cache.put(first, "aaaa")
    cache.put(second, "bbbb")
    assert cache.get(first, amap) == "aaaa"

    cache.put(third, "cccc")

    assert cache.get(second, amap) is None
    assert cache.get(first, amap) == "aaaa"
    assert cache.get(third, amap) == "cccc"
    assert cache.stored_chars == 8
    cache.put(first, "a")
    assert cache.stored_chars == 5
    cache.put(RemaskCache.key(1, 9, "p", "t", "en"), "x" * 11)
    assert len(cache) == 2
    cache.forget_document(1)
    assert cache.stored_chars == 0


async def test_policy_change_between_reads_is_not_cached_under_the_old_key(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from septum_api.services.policy_snapshot import current_policy_snapshot

    seen: dict[str, Any] = {}

    async def _snapshot_then_policy_changes(db):
        snapshot = await current_policy_snapshot(db)
        seen.setdefault("before", snapshot)
        async with env["sm"]() as session:
            await session.execute(
                update(RegulationRuleset)
                .where(RegulationRuleset.id == "reg_contacts")
                .values(is_active=True)
            )
            await session.commit()
        return snapshot

    monkeypatch.setattr(chat_module, "current_policy_snapshot", _snapshot_then_policy_changes)

    await _ask(env)

    async with env["sm"]() as session:
        after = await current_policy_snapshot(session)
        chunk = (await session.execute(select(Chunk))).scalars().one()
    amap = await document_anon_store.get_document_map(env["doc_id"])
    old_key = remask_cache.key(env["doc_id"], chunk.id, seen["before"].cache_key, STORED_CHUNK, "en")
    new_key = remask_cache.key(env["doc_id"], chunk.id, after.cache_key, STORED_CHUNK, "en")
    assert seen["before"].cache_key != after.cache_key
    assert remask_cache.get(old_key, amap) is None
    cached = remask_cache.get(new_key, amap)
    assert cached is not None and EMAIL_VALUE not in cached
    assert EMAIL_VALUE not in env["prompts"][-1]


async def _events(env: dict[str, Any]) -> list[dict[str, Any]]:
    resp = await env["client"].post(
        "/api/chat/ask",
        json={"message": "When was the visit?", "document_ids": [env["doc_id"]]},
    )
    assert resp.status_code == 200, resp.text
    return [
        json.loads(line[len("data: "):])
        for line in resp.text.splitlines()
        if line.startswith("data: ")
    ]


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (pii_gate.DocumentMapMissingError("Document 1 has no anonymization map"), "document_changed"),
        (pii_gate.DocumentGoneError(), "document_changed"),
        (pii_gate.DocumentMapConflictError(), "map_conflict"),
    ],
)
async def test_map_failure_is_a_typed_error_and_nothing_is_sent(
    env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    code: str,
) -> None:
    async def _fail(*args: Any, **kwargs: Any):
        raise error

    monkeypatch.setattr(chat_module, "remask_document_texts", _fail)

    events = await _events(env)

    [event] = [e for e in events if e["type"] == "error"]
    assert event["code"] == code
    assert "anonymization map" not in event["message"]
    assert "Document 1" not in event["message"]
    assert not [e for e in events if e["type"] == "answer_chunk"]
    assert not env["prompts"]


async def test_map_erased_before_the_remask_is_document_changed(
    env: dict[str, Any],
) -> None:
    await document_anon_store.pop_document_map(env["doc_id"])

    events = await _events(env)

    [event] = [e for e in events if e["type"] == "error"]
    assert event["code"] == chat_module.DOCUMENT_CHANGED
    assert not env["prompts"]


async def _align_with_current_policy(env: dict[str, Any], **changes: Any) -> None:
    from septum_api.services.policy_snapshot import current_policy_snapshot

    async with env["sm"]() as session:
        snapshot = await current_policy_snapshot(session)
        await session.execute(
            update(Document)
            .where(Document.id == env["doc_id"])
            .values(
                sanitized_entity_types=snapshot.entity_types,
                sanitized_policy_digest=snapshot.digest,
                **changes,
            )
        )
        await session.commit()


async def test_unrecorded_masking_language_is_stale_through_the_chat_path(env: dict[str, Any]) -> None:
    await _align_with_current_policy(env, sanitized_language="en")
    await _ask(env)
    assert _chunk_sanitize_calls(env) == 0

    await _align_with_current_policy(env, sanitized_language=None)
    await _ask(env)
    assert _chunk_sanitize_calls(env) == 1


async def test_remask_cache_is_keyed_by_the_masking_language_through_the_chat_path(
    env: dict[str, Any],
) -> None:
    await _align_with_current_policy(env, sanitized_language="en", language_override="de")
    await _ask(env)
    await _ask(env)
    assert _chunk_sanitize_calls(env) == 1

    await _align_with_current_policy(env, sanitized_language="en", language_override="tr")
    await _ask(env)
    assert _chunk_sanitize_calls(env) == 2
