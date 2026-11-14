"""A reingest claim clears the column labels that hold placeholders of the erased map.

The claim erases the document's anonymization map and the rebuild numbers it
afresh, so a placeholder kept in a typed column label could later restore to
a different person. The claim tests run on SQLite and, when
``SEPTUM_TEST_POSTGRES_URL`` is set, on PostgreSQL; the end-to-end reprocess
runs a real ``DocumentPipeline`` on SQLite with pattern-double detectors.
"""
from __future__ import annotations

import asyncio
import copy
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator, Optional

import pytest
from httpx import ASGITransport, AsyncClient
from sanitizer_doubles import PatternDetector, PatternSanitizer, literal
from septum_api import database as database_module
from septum_api.database import _sqlite_wal_connect, build_default_app_settings, get_db
from septum_api.main import app
from septum_api.models import Base
from septum_api.models.document import Document
from septum_api.models.spreadsheet_schema import SpreadsheetColumn, SpreadsheetSchema
from septum_api.models.user import User
from septum_api.routers import chat as chat_module
from septum_api.routers import documents as documents_module
from septum_api.services import document_anon_store, pii_gate
from septum_api.services import document_pipeline as dp
from septum_api.services.chunking import ChunkRecord
from septum_api.services.deanonymizer import Deanonymizer
from septum_api.services.ingestion.base import IngestionResult, TextBlock, join_blocks
from septum_api.utils.auth_dependency import get_current_user
from sqlalchemy import event, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

POSTGRES_URL = os.getenv("SEPTUM_TEST_POSTGRES_URL", "")

PLACEHOLDER_LABEL = "[PERSON_NAME_3] bonus"
PLAIN_LABEL = "Salary"


@asynccontextmanager
async def _sqlite_engine(tmp_path: Path) -> AsyncIterator[Any]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'claim.db'}")
    event.listen(engine.sync_engine, "connect", _sqlite_wal_connect)
    try:
        yield engine
    finally:
        await engine.dispose()


@asynccontextmanager
async def _postgres_engine() -> AsyncIterator[Any]:
    schema = f"reingest_labels_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(POSTGRES_URL)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        POSTGRES_URL, connect_args={"server_settings": {"search_path": schema}}
    )
    try:
        yield engine
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@pytest.fixture(params=["sqlite", "postgresql"])
async def claim_env(request, tmp_path, monkeypatch):
    """A completed spreadsheet document with a placeholder, a plain and an unlabelled column."""
    if request.param == "postgresql" and not POSTGRES_URL:
        pytest.skip("SEPTUM_TEST_POSTGRES_URL not set")
    async with (
        _sqlite_engine(tmp_path) if request.param == "sqlite" else _postgres_engine()
    ) as engine:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with maker() as session:
            user = User(email="editor@example.com", hashed_password="x", role="editor")
            session.add(user)
            session.add(build_default_app_settings())
            await session.commit()
            doc = Document(
                filename="s.xlsx", original_filename="s.xlsx", file_type="x",
                file_format="xlsx", detected_language="en", encrypted_path="/x",
                file_size_bytes=1, ingestion_status="completed", user_id=user.id,
            )
            session.add(doc)
            await session.flush()
            schema = SpreadsheetSchema(document_id=doc.id)
            session.add(schema)
            await session.flush()
            session.add_all([
                SpreadsheetColumn(schema_id=schema.id, index=0, technical_label="COLUMN_1",
                                  semantic_label=PLACEHOLDER_LABEL, is_numeric=True),
                SpreadsheetColumn(schema_id=schema.id, index=1, technical_label="COLUMN_2",
                                  semantic_label=PLAIN_LABEL, is_numeric=False),
                SpreadsheetColumn(schema_id=schema.id, index=2, technical_label="COLUMN_3",
                                  semantic_label=None, is_numeric=None),
            ])
            await session.commit()
            doc_id = doc.id

        async def _get_db():
            async with maker() as s:
                yield s

        async def _gate_as_typed(db, settings, document, texts, sanitizer=None, joined=False):
            return list(texts)

        monkeypatch.setattr(documents_module, "gate_document_texts", _gate_as_typed)
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            app.dependency_overrides[get_db] = _get_db
            app.dependency_overrides[get_current_user] = lambda: user
            try:
                yield SimpleNamespace(
                    maker=maker, client=client, doc_id=doc_id, monkeypatch=monkeypatch,
                    engine=engine, backend=request.param,
                )
            finally:
                app.dependency_overrides.pop(get_db, None)
                app.dependency_overrides.pop(get_current_user, None)


async def _columns(maker, doc_id) -> dict[int, tuple[Optional[str], Optional[bool]]]:
    async with maker() as session:
        rows = await session.execute(
            select(SpreadsheetColumn.index, SpreadsheetColumn.semantic_label,
                   SpreadsheetColumn.is_numeric)
            .join(SpreadsheetSchema)
            .where(SpreadsheetSchema.document_id == doc_id)
        )
        return {index: (label, numeric) for index, label, numeric in rows.all()}


async def _claim(maker, doc_id) -> Optional[str]:
    async with maker() as session:
        return await documents_module.claim_for_reingest(session, doc_id)


async def test_claim_clears_only_the_placeholder_label(claim_env) -> None:
    assert await _claim(claim_env.maker, claim_env.doc_id) is not None

    assert await _columns(claim_env.maker, claim_env.doc_id) == {
        0: (None, True),
        1: (PLAIN_LABEL, False),
        2: (None, None),
    }


async def test_claim_clears_placeholders_of_custom_entity_types(claim_env) -> None:
    async with claim_env.maker() as session:
        await session.execute(
            update(SpreadsheetColumn)
            .where(SpreadsheetColumn.index == 0)
            .values(semantic_label="[MRN2_1] bonus")
        )
        await session.execute(
            update(SpreadsheetColumn)
            .where(SpreadsheetColumn.index == 2)
            .values(semantic_label="[Project code_1]")
        )
        await session.commit()

    assert await _claim(claim_env.maker, claim_env.doc_id) is not None

    assert await _columns(claim_env.maker, claim_env.doc_id) == {
        0: (None, True),
        1: (PLAIN_LABEL, False),
        2: (None, None),
    }


async def test_lost_claim_leaves_the_labels(claim_env) -> None:
    async with claim_env.maker() as session:
        doc = await session.get(Document, claim_env.doc_id)
        doc.ingestion_status = "processing"
        await session.commit()
    before = await _columns(claim_env.maker, claim_env.doc_id)

    assert await _claim(claim_env.maker, claim_env.doc_id) is None

    assert await _columns(claim_env.maker, claim_env.doc_id) == before
    assert before[0] == (PLACEHOLDER_LABEL, True)


async def test_schema_still_lists_every_column_after_the_claim(claim_env) -> None:
    await _claim(claim_env.maker, claim_env.doc_id)

    resp = await claim_env.client.get(f"/api/documents/{claim_env.doc_id}/schema")

    assert resp.status_code == 200
    assert [
        (c["index"], c["technical_label"], c["semantic_label"]) for c in resp.json()["columns"]
    ] == [(0, "COLUMN_1", None), (1, "COLUMN_2", PLAIN_LABEL), (2, "COLUMN_3", None)]


def _placeholder_put(env):
    return env.client.put(
        f"/api/documents/{env.doc_id}/schema",
        json={"columns": [{"index": 0, "technical_label": "COLUMN_1",
                           "semantic_label": "[PERSON_NAME_4] bonus", "is_numeric": True}]},
    )


async def test_claim_before_the_put_holds_the_document_refuses_the_put(claim_env) -> None:
    real_hold = documents_module.hold_completed_document

    async def _claim_then_hold(db, document_id, run_token):
        assert await _claim(claim_env.maker, document_id) is not None
        await real_hold(db, document_id, run_token)

    claim_env.monkeypatch.setattr(documents_module, "hold_completed_document", _claim_then_hold)

    resp = await _placeholder_put(claim_env)

    assert resp.status_code == 409
    assert (await _columns(claim_env.maker, claim_env.doc_id))[0] == (None, True)


# The claim sets the status; the hold only rewrites the run token in place.
_CLAIM_STATEMENT = " ingestion_status="
_HOLD_STATEMENT = "SET ingestion_run_id=documents.ingestion_run_id"


async def _until_a_backend_waits_on_a_lock(maker) -> None:
    """Return once a PostgreSQL backend of this database is blocked on a lock."""
    while True:
        async with maker() as session:
            waiting = await session.scalar(text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            ))
        if waiting:
            return
        await asyncio.sleep(0.02)


async def test_claim_issued_while_a_put_holds_the_document_lands_after_it_and_clears_its_label(
    claim_env,
) -> None:
    """The claim is sent while the PUT holds the document row; it returns only after the PUT commits.

    The order is read from engine events, not timing. On PostgreSQL the test
    also waits until the claim is seen blocked on a row lock before the PUT
    goes on, so the wait itself is observed there.
    """
    order: list[str] = []
    claim_sent = asyncio.Event()
    put_connection: list[Any] = []

    def _before(conn, cursor, statement, parameters, context, executemany):
        if _HOLD_STATEMENT in statement:
            put_connection.append(conn.connection.dbapi_connection)
        elif _CLAIM_STATEMENT in statement:
            claim_sent.set()

    def _after(conn, cursor, statement, parameters, context, executemany):
        if _CLAIM_STATEMENT in statement:
            order.append("claim returned")

    def _commit(conn):
        if put_connection and conn.connection.dbapi_connection is put_connection[0]:
            order.append("put committed")

    sync_engine = claim_env.engine.sync_engine
    event.listen(sync_engine, "before_cursor_execute", _before)
    event.listen(sync_engine, "after_cursor_execute", _after)
    event.listen(sync_engine, "commit", _commit)
    real_hold = documents_module.hold_completed_document
    claims: list[asyncio.Task] = []

    async def _hold_then_claim(db, document_id, run_token):
        await real_hold(db, document_id, run_token)
        claims.append(asyncio.create_task(_claim(claim_env.maker, document_id)))
        await asyncio.wait_for(claim_sent.wait(), 30)
        if claim_env.backend == "postgresql":
            await asyncio.wait_for(_until_a_backend_waits_on_a_lock(claim_env.maker), 30)

    claim_env.monkeypatch.setattr(documents_module, "hold_completed_document", _hold_then_claim)
    try:
        resp = await _placeholder_put(claim_env)
        token = await asyncio.wait_for(claims[0], 30)
    finally:
        event.remove(sync_engine, "before_cursor_execute", _before)
        event.remove(sync_engine, "after_cursor_execute", _after)
        event.remove(sync_engine, "commit", _commit)

    assert resp.status_code == 200
    assert resp.json()["columns"][0]["semantic_label"] == "[PERSON_NAME_4] bonus"
    assert token is not None
    assert order[:2] == ["put committed", "claim returned"]
    assert (await _columns(claim_env.maker, claim_env.doc_id))[0] == (None, True)


TEXT = "Jane Roe signed the lease. Witness Carl Doe was present. Ahmet Kaya signed."
ANSWER = "That column belongs to [PERSON_NAME_3]."


class _NoopIndex:
    def delete_index(self, document_id):
        return None


@pytest.fixture
async def pipeline_env(tmp_path, monkeypatch):
    """SQLite with the real pipeline, map store and map lock; detectors are pattern doubles."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pipeline.db'}")
    event.listen(engine.sync_engine, "connect", _sqlite_wal_connect)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with maker() as session:
        user = User(email="owner@example.com", hashed_password="x", role="editor")
        session.add(user)
        await session.commit()

    async def _get_db():
        async with maker() as s:
            yield s

    async def _noop(*args, **kwargs):
        return None

    label_sanitizer = PatternSanitizer(literal("Alice Smith", "PERSON_NAME"))

    async def _gate_sanitizer(db, settings, enable_ollama=False, policy=None, context_gate=True):
        return label_sanitizer

    monkeypatch.setattr(document_anon_store, "_ANON_MAP_DIR", tmp_path / "maps")
    monkeypatch.setattr(document_anon_store, "_document_maps", {})
    monkeypatch.setattr(document_anon_store, "_map_versions", {})
    monkeypatch.setattr(dp, "VectorStore", _NoopIndex)
    monkeypatch.setattr(dp, "BM25Retriever", _NoopIndex)
    monkeypatch.setattr(dp, "replace_index_for_document", _noop)
    monkeypatch.setattr(dp, "recompute_relationships_for_document", _noop)
    monkeypatch.setattr(dp.DocumentPipeline, "_index_document", _noop)
    monkeypatch.setattr(pii_gate, "create_sanitizer", _gate_sanitizer)
    monkeypatch.setattr(database_module, "get_session_maker", lambda: maker)
    monkeypatch.setattr(
        dp.DocumentPipeline,
        "_chunk_records",
        lambda self, fmt, blocks, protected: [
            ChunkRecord(index=0, start=0, end=len(TEXT), text=join_blocks(blocks), strategy="sliding")
        ],
    )
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        app.dependency_overrides[get_db] = _get_db
        app.dependency_overrides[get_current_user] = lambda: user
        try:
            yield SimpleNamespace(maker=maker, client=client, user=user, monkeypatch=monkeypatch)
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)
            await engine.dispose()


def _detect(env, *names: str) -> None:
    detector = PatternDetector(*(literal(name, "PERSON_NAME") for name in names))

    async def _pipeline_sanitizer(db, settings, enable_ollama=None, policy=None):
        return detector

    env.monkeypatch.setattr(dp, "create_sanitizer", _pipeline_sanitizer)


def _ingestion_result() -> IngestionResult:
    return IngestionResult(text=TEXT, metadata={}, blocks=[TextBlock(TEXT, "text")])


async def _run(session, document, token) -> None:
    await dp.DocumentPipeline(settings=build_default_app_settings()).run(
        db=session, document=document, file_format="xlsx",
        ingestion_result=_ingestion_result(), run_token=token,
    )


async def _chat_turn(env, doc_id) -> tuple[list[str], str]:
    """The chat path a column label takes: unified map, described columns, de-anonymized answer."""
    settings = build_default_app_settings()
    query_map = copy.deepcopy(await document_anon_store.get_document_map(doc_id))
    _, _, _, unified = await chat_module._unify_turn_placeholders(query_map, [doc_id], "en")
    async with env.maker() as session:
        descriptions = await chat_module._schema_column_descriptions(
            session, doc_id, unified, settings
        )
    sent = chat_module._sent_placeholders("q", [], descriptions, [])
    restored = Deanonymizer(settings=settings).deanonymize(
        ANSWER, unified, allowed_placeholders=sent
    )
    return descriptions, restored


async def test_wider_policy_reprocess_never_restores_the_old_label_to_another_person(
    pipeline_env,
) -> None:
    env = pipeline_env
    _detect(env, "Jane Roe", "Carl Doe")
    async with env.maker() as session:
        document = Document(
            filename="f", original_filename="f", file_type="x", file_format="xlsx",
            detected_language="en", encrypted_path="/x", file_size_bytes=1,
            ingestion_status="processing", ingestion_run_id="run-1", user_id=env.user.id,
        )
        session.add(document)
        await session.commit()
        await documents_module._ensure_spreadsheet_schema(
            session, document.id, {"sheets": [{"column_count": 2}]}
        )
        await _run(session, document, "run-1")
        doc_id = document.id
    resp = await env.client.put(f"/api/documents/{doc_id}/schema", json={"columns": [
        {"index": 0, "technical_label": "COLUMN_1", "semantic_label": "Alice Smith bonus",
         "is_numeric": True}]})
    assert resp.status_code == 200
    assert resp.json()["columns"][0]["semantic_label"] == PLACEHOLDER_LABEL

    _detect(env, "Jane Roe", "Carl Doe", "Ahmet Kaya")
    async with env.maker() as session:
        token = await documents_module.claim_for_reingest(session, doc_id)
        assert token is not None
        document = await session.get(Document, doc_id)
        await dp.DocumentPipeline.cleanup_artifacts(doc_id, db=session, run_token=token)
        await _run(session, document, token)
    rebuilt = await document_anon_store.load_persisted_document_map(doc_id)
    assert rebuilt.entity_map["Ahmet Kaya"] == "[PERSON_NAME_3]"

    descriptions, restored = await _chat_turn(env, doc_id)

    assert descriptions == ["index 0 → COLUMN_1 (numeric)", "index 1 → COLUMN_2"]
    assert "Ahmet Kaya" not in restored
    assert restored == ANSWER
