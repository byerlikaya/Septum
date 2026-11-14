"""Chunk and schema edits are accepted only on a document whose ingestion completed.

A real ``DocumentPipeline.run`` on SQLite (WAL + foreign keys) with the real
map store and map lock; the API, called through ASGI inside the run (or
inside another request) at a hook point, acts as a second session.
Detectors are pattern doubles.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sanitizer_doubles import PatternDetector, PatternSanitizer, literal
from septum_api import database as database_module
from septum_api.database import _sqlite_wal_connect, build_default_app_settings, get_db
from septum_api.main import app
from septum_api.models import Base
from septum_api.models.document import Chunk, Document
from septum_api.models.spreadsheet_schema import SpreadsheetSchema
from septum_api.models.user import User
from septum_api.routers import chunks as chunks_module
from septum_api.routers import documents as documents_module
from septum_api.services import document_anon_store, pii_gate
from septum_api.services import document_pipeline as dp
from septum_api.services.chunking import ChunkRecord
from septum_api.services.ingestion.base import IngestionResult, TextBlock, join_blocks
from septum_api.utils.auth_dependency import get_current_user
from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

DOC_NAME = "Jane Roe"
SECOND_CHUNK = "Rent is due on the first day of each month."
TEXT = f"{DOC_NAME} signed the lease on Monday. " + SECOND_CHUNK
NOT_EDITABLE = {"detail": "Only a document that finished processing can be edited."}
JANE_ONLY = {DOC_NAME: "[PERSON_NAME_1]"}
NOT_COMPLETED = ("pending", "processing", "failed")
LABEL_PAYLOAD = {"columns": [{"index": 0, "technical_label": "COLUMN_1", "semantic_label": "Alice Smith"}]}


class _NoopIndex:
    def delete_index(self, document_id):
        return None


@pytest.fixture
async def env(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'edits.db'}")
    event.listen(engine.sync_engine, "connect", _sqlite_wal_connect)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _get_db():
        async with maker() as session:
            yield session

    async with maker() as session:
        user = User(email="owner@example.com", hashed_password="x", role="editor")
        session.add(user)
        await session.commit()

    async def _noop(*args, **kwargs):
        return None

    detector = PatternDetector(literal(DOC_NAME, "PERSON_NAME"))

    async def _pipeline_sanitizer(db, settings, enable_ollama=None, policy=None):
        return detector

    user_sanitizer = PatternSanitizer(
        literal("Alice Smith", "PERSON_NAME"), literal("Bob Jones", "PERSON_NAME")
    )

    async def _gate_sanitizer(db, settings, enable_ollama=False, policy=None, context_gate=True):
        return user_sanitizer

    monkeypatch.setattr(document_anon_store, "_ANON_MAP_DIR", tmp_path / "maps")
    monkeypatch.setattr(document_anon_store, "_document_maps", {})
    monkeypatch.setattr(document_anon_store, "_map_versions", {})
    monkeypatch.setattr(dp, "VectorStore", _NoopIndex)
    monkeypatch.setattr(dp, "BM25Retriever", _NoopIndex)
    monkeypatch.setattr(dp, "create_sanitizer", _pipeline_sanitizer)
    monkeypatch.setattr(dp, "replace_index_for_document", _noop)
    monkeypatch.setattr(dp, "recompute_relationships_for_document", _noop)
    monkeypatch.setattr(dp.DocumentPipeline, "_index_document", _noop)
    monkeypatch.setattr(pii_gate, "create_sanitizer", _gate_sanitizer)
    monkeypatch.setattr(database_module, "get_session_maker", lambda: maker)
    cut = TEXT.index(SECOND_CHUNK)
    monkeypatch.setattr(
        dp.DocumentPipeline,
        "_chunk_records",
        lambda self, fmt, blocks, protected: [
            ChunkRecord(index=0, start=0, end=cut, text=join_blocks(blocks)[:cut], strategy="sliding"),
            ChunkRecord(
                index=1, start=cut, end=len(TEXT), text=join_blocks(blocks)[cut:], strategy="sliding"
            ),
        ],
    )
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        app.dependency_overrides[get_db] = _get_db
        app.dependency_overrides[get_current_user] = lambda: user
        try:
            yield SimpleNamespace(
                maker=maker, client=client, user=user, monkeypatch=monkeypatch, engine=engine
            )
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)
            await engine.dispose()


def _ingestion_result():
    return IngestionResult(text=TEXT, metadata={}, blocks=[TextBlock(TEXT, "text")])


async def _ingest(env, token="run-1"):
    """Create a ``processing`` document and run the pipeline on it; return its id."""
    async with env.maker() as session:
        document = Document(
            filename="f", original_filename="f", file_type="x", file_format="image",
            detected_language="en", encrypted_path="/x", file_size_bytes=1,
            ingestion_status="processing", ingestion_run_id=token, user_id=env.user.id,
        )
        session.add(document)
        await session.commit()
        await dp.DocumentPipeline(settings=build_default_app_settings()).run(
            db=session, document=document, file_format="image",
            ingestion_result=_ingestion_result(), run_token=token,
        )
        return document.id


async def _reprocess(env, document_id):
    """Claim the document for a rebuild and run the pipeline to completion again."""
    async with env.maker() as session:
        token = await documents_module.claim_for_reingest(session, document_id)
        assert token is not None
        document = await session.get(Document, document_id)
        await dp.DocumentPipeline(settings=build_default_app_settings()).run(
            db=session, document=document, file_format="image",
            ingestion_result=_ingestion_result(), run_token=token,
        )


def _after_chunk_insert(env, action):
    """Run ``action(chunk_ids, document_id)`` inside the run, right after its chunks commit."""
    original = dp.DocumentPipeline._persist_chunks

    async def _wrapped(self, db, chunks):
        stored = await original(self, db, chunks)
        await action([c.id for c in stored], chunks[0].document_id)
        return stored

    env.monkeypatch.setattr(dp.DocumentPipeline, "_persist_chunks", _wrapped)


async def _set_status(env, document_id, status, **values):
    async with env.maker() as session:
        await session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(ingestion_status=status, **values)
        )
        await session.commit()


async def _document(env, document_id):
    async with env.maker() as session:
        return await session.get(Document, document_id)


async def _chunks(env, document_id):
    async with env.maker() as session:
        rows = await session.execute(
            select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.index)
        )
        return list(rows.scalars())


async def _schema_rows(env, document_id):
    async with env.maker() as session:
        return await session.scalar(
            select(func.count())
            .select_from(SpreadsheetSchema)
            .where(SpreadsheetSchema.document_id == document_id)
        )


async def _entity_map(document_id):
    stored = await document_anon_store.load_persisted_document_map(document_id)
    return None if stored is None else dict(stored.entity_map)


async def _assert_untouched(env, document_id, status):
    document = await _document(env, document_id)
    assert (document.ingestion_status, document.chunk_count) == (status, 2)
    first, second = await _chunks(env, document_id)
    assert first.sanitized_text == "[PERSON_NAME_1] signed the lease on Monday. "
    assert second.sanitized_text == SECOND_CHUNK
    assert await _schema_rows(env, document_id) == 0


async def test_edit_of_a_completed_document_still_extends_its_map(env):
    document_id = await _ingest(env)
    first, _ = await _chunks(env, document_id)

    response = await env.client.patch(
        f"/api/chunks/{first.id}", json={"sanitized_text": first.sanitized_text + " Witness: Bob Jones."}
    )

    assert response.status_code == 200
    assert response.json()["sanitized_text"].endswith("Witness: [PERSON_NAME_2].")
    assert await _entity_map(document_id) == {
        DOC_NAME: "[PERSON_NAME_1]",
        "Bob Jones": "[PERSON_NAME_2]",
    }


async def test_section_title_only_edit_of_a_completed_document_is_stored(env):
    document_id = await _ingest(env)
    first, _ = await _chunks(env, document_id)

    response = await env.client.patch(f"/api/chunks/{first.id}", json={"section_title": "Lease terms"})

    assert response.status_code == 200
    assert response.json()["section_title"] == "Lease terms"
    first, _ = await _chunks(env, document_id)
    assert first.section_title == "Lease terms"


@pytest.mark.parametrize("status", [*NOT_COMPLETED, "completed"])
async def test_empty_edit_is_refused_unless_the_document_completed(env, status):
    document_id = await _ingest(env)
    first, _ = await _chunks(env, document_id)
    await _set_status(env, document_id, status)

    response = await env.client.patch(f"/api/chunks/{first.id}", json={})

    assert response.status_code == (200 if status == "completed" else 409)
    await _assert_untouched(env, document_id, status)


@pytest.mark.parametrize("status", NOT_COMPLETED)
async def test_edit_and_delete_are_refused_unless_the_document_completed(env, status):
    document_id = await _ingest(env)
    first, second = await _chunks(env, document_id)
    await _set_status(env, document_id, status)

    edit = await env.client.patch(f"/api/chunks/{second.id}", json={"sanitized_text": "Paid Alice Smith."})
    removal = await env.client.delete(f"/api/chunks/{first.id}")

    assert (edit.status_code, removal.status_code) == (409, 409)
    await _assert_untouched(env, document_id, status)
    assert await _entity_map(document_id) == JANE_ONLY


@pytest.mark.parametrize("status", ["pending", "failed"])
async def test_schema_label_is_refused_unless_the_document_completed(env, status):
    document_id = await _ingest(env)
    await _set_status(env, document_id, status)

    response = await env.client.put(f"/api/documents/{document_id}/schema", json=LABEL_PAYLOAD)

    assert response.status_code == 409
    await _assert_untouched(env, document_id, status)
    assert await _entity_map(document_id) == JANE_ONLY


async def test_refusals_carry_only_the_fixed_detail(env):
    document_id = await _ingest(env)
    first, second = await _chunks(env, document_id)
    await _set_status(env, document_id, "processing")

    responses = [
        await env.client.patch(f"/api/chunks/{second.id}", json={"sanitized_text": "Paid Alice Smith."}),
        await env.client.delete(f"/api/chunks/{first.id}"),
        await env.client.put(f"/api/documents/{document_id}/schema", json=LABEL_PAYLOAD),
    ]

    for response in responses:
        assert response.status_code == 409
        assert response.json() == NOT_EDITABLE
        assert "Alice" not in response.text


@pytest.mark.parametrize("status", ["processing", "completed"])
async def test_a_non_owner_gets_404_whatever_the_status(env, status):
    document_id = await _ingest(env)
    first, second = await _chunks(env, document_id)
    await _set_status(env, document_id, status)
    async with env.maker() as session:
        stranger = User(email="other@example.com", hashed_password="x", role="editor")
        session.add(stranger)
        await session.commit()
    app.dependency_overrides[get_current_user] = lambda: stranger

    edit = await env.client.patch(f"/api/chunks/{second.id}", json={"sanitized_text": "Paid Alice Smith."})
    removal = await env.client.delete(f"/api/chunks/{first.id}")
    schema = await env.client.put(f"/api/documents/{document_id}/schema", json=LABEL_PAYLOAD)

    assert (edit.status_code, removal.status_code, schema.status_code) == (404, 404, 404)
    await _assert_untouched(env, document_id, status)
    assert await _entity_map(document_id) == JANE_ONLY


async def test_edit_during_a_run_is_refused_so_no_placeholder_is_reused(env):
    seen = {}

    async def _edit(chunk_ids, document_id):
        response = await env.client.patch(
            f"/api/chunks/{chunk_ids[1]}", json={"sanitized_text": "Rent is paid to Alice Smith."}
        )
        seen["status"] = response.status_code

    _after_chunk_insert(env, _edit)
    document_id = await _ingest(env)
    first, second = await _chunks(env, document_id)

    assert seen["status"] == 409
    assert (await _document(env, document_id)).ingestion_status == "completed"
    assert second.sanitized_text == SECOND_CHUNK
    assert await _entity_map(document_id) == JANE_ONLY

    later = await env.client.patch(
        f"/api/chunks/{first.id}", json={"sanitized_text": first.sanitized_text + " Witness: Bob Jones."}
    )
    assert later.status_code == 200
    _, second = await _chunks(env, document_id)
    assert "[PERSON_NAME_2]" not in second.sanitized_text


async def test_edit_of_a_failed_document_does_not_recreate_its_erased_map(env):
    document_id = await _ingest(env)
    _, second = await _chunks(env, document_id)
    await _set_status(env, document_id, "failed")
    await document_anon_store.pop_document_map(document_id)

    response = await env.client.patch(f"/api/chunks/{second.id}", json={"sanitized_text": "Paid Alice Smith."})

    assert response.status_code == 409
    assert await _entity_map(document_id) is None
    _, second = await _chunks(env, document_id)
    assert second.sanitized_text == SECOND_CHUNK


async def test_edit_racing_the_failure_purge_is_refused_without_a_map(env):
    document_id = await _ingest(env)
    _, second = await _chunks(env, document_id)
    real_gate = chunks_module.gate_document_texts

    async def _gate_after_purge(db, settings, document, texts, **kwargs):
        await _set_status(env, document_id, "failed")
        async with env.maker() as other:
            assert await dp.purge_failed_derived_data(other, document.id)
        return await real_gate(db, settings, document, texts, **kwargs)

    env.monkeypatch.setattr(chunks_module, "gate_document_texts", _gate_after_purge)

    response = await env.client.patch(f"/api/chunks/{second.id}", json={"sanitized_text": "Paid Alice Smith."})

    assert response.status_code == 409
    assert await _entity_map(document_id) is None


async def test_a_claim_landing_while_the_edit_is_gated_leaves_the_map_alone(env):
    document_id = await _ingest(env)
    _, second = await _chunks(env, document_id)
    real_gate_untrusted = pii_gate.gate_untrusted_texts

    async def _claimed_while_gating(*args, **kwargs):
        gated = await real_gate_untrusted(*args, **kwargs)
        await _set_status(env, document_id, "processing")
        return gated

    env.monkeypatch.setattr(pii_gate, "gate_untrusted_texts", _claimed_while_gating)

    response = await env.client.patch(f"/api/chunks/{second.id}", json={"sanitized_text": "Paid Alice Smith."})

    assert response.status_code == 409
    assert await _entity_map(document_id) == JANE_ONLY
    _, second = await _chunks(env, document_id)
    assert second.sanitized_text == SECOND_CHUNK


async def test_a_claim_landing_after_the_gate_refuses_the_chunk_write(env):
    document_id = await _ingest(env)
    _, second = await _chunks(env, document_id)
    real_gate = chunks_module.gate_document_texts

    async def _claimed_after_gate(db, settings, document, texts, **kwargs):
        gated = await real_gate(db, settings, document, texts, **kwargs)
        await _set_status(env, document_id, "processing")
        return gated

    env.monkeypatch.setattr(chunks_module, "gate_document_texts", _claimed_after_gate)

    response = await env.client.patch(f"/api/chunks/{second.id}", json={"sanitized_text": "Paid Alice Smith."})

    assert response.status_code == 409
    _, second = await _chunks(env, document_id)
    assert second.sanitized_text == SECOND_CHUNK


async def test_a_reprocess_completing_while_the_edit_is_gated_leaves_the_map_alone(env):
    document_id = await _ingest(env)
    _, second = await _chunks(env, document_id)
    real_gate_untrusted = pii_gate.gate_untrusted_texts
    reprocessed = []

    async def _reprocessed_while_gating(*args, **kwargs):
        gated = await real_gate_untrusted(*args, **kwargs)
        if not reprocessed:
            reprocessed.append(True)
            await _reprocess(env, document_id)
        return gated

    env.monkeypatch.setattr(pii_gate, "gate_untrusted_texts", _reprocessed_while_gating)

    response = await env.client.patch(f"/api/chunks/{second.id}", json={"sanitized_text": "Paid Alice Smith."})

    assert response.status_code == 409
    assert await _entity_map(document_id) == JANE_ONLY
    _, second = await _chunks(env, document_id)
    assert second.sanitized_text == SECOND_CHUNK


async def test_a_reprocess_completing_after_the_gate_refuses_the_chunk_write(env):
    document_id = await _ingest(env)
    before = await _chunks(env, document_id)
    real_gate = chunks_module.gate_document_texts

    async def _reprocessed_after_gate(db, settings, document, texts, **kwargs):
        gated = await real_gate(db, settings, document, texts, **kwargs)
        await _reprocess(env, document_id)
        return gated

    env.monkeypatch.setattr(chunks_module, "gate_document_texts", _reprocessed_after_gate)

    response = await env.client.patch(
        f"/api/chunks/{before[1].id}", json={"sanitized_text": "Paid Alice Smith."}
    )

    assert response.status_code == 409
    after = await _chunks(env, document_id)
    assert [c.id for c in after] == [c.id for c in before]
    assert after[1].sanitized_text == SECOND_CHUNK
    document = await _document(env, document_id)
    assert document.ingestion_status == "completed"
    assert document.ingestion_run_id != "run-1"


async def test_a_reprocess_completing_before_the_delete_keeps_the_chunk(env):
    document_id = await _ingest(env)
    before = await _chunks(env, document_id)
    real_completed = chunks_module._completed_document

    async def _reprocessed_after_read(db, chunk):
        document = await real_completed(db, chunk)
        await _reprocess(env, document_id)
        return document

    env.monkeypatch.setattr(chunks_module, "_completed_document", _reprocessed_after_read)

    response = await env.client.delete(f"/api/chunks/{before[0].id}")

    assert response.status_code == 409
    assert [c.id for c in await _chunks(env, document_id)] == [c.id for c in before]
    assert (await _document(env, document_id)).chunk_count == 2


async def test_gate_document_texts_refuses_a_document_that_is_not_completed(env):
    document_id = await _ingest(env)
    for status in NOT_COMPLETED:
        await _set_status(env, document_id, status)
        async with env.maker() as session:
            document = await session.get(Document, document_id)
            with pytest.raises(pii_gate.DocumentNotEditableError) as raised:
                await pii_gate.gate_document_texts(
                    session, build_default_app_settings(), document, ["Paid Alice Smith."]
                )
        assert raised.value.status_code == 409
        assert await _entity_map(document_id) == JANE_ONLY


async def test_gate_refuses_a_failed_document_before_building_a_sanitizer(env):
    document_id = await _ingest(env)
    built = []
    real_create = pii_gate.create_user_text_sanitizer

    async def _spy(*args, **kwargs):
        built.append(True)
        return await real_create(*args, **kwargs)

    env.monkeypatch.setattr(pii_gate, "create_user_text_sanitizer", _spy)
    await _set_status(env, document_id, "failed")
    async with env.maker() as session:
        document = await session.get(Document, document_id)
        with pytest.raises(pii_gate.DocumentNotEditableError):
            await pii_gate.gate_document_texts(
                session, build_default_app_settings(), document, ["Paid Alice Smith."]
            )
    assert built == []

    await _set_status(env, document_id, "completed")
    async with env.maker() as session:
        document = await session.get(Document, document_id)
        await pii_gate.gate_document_texts(
            session, build_default_app_settings(), document, ["Paid Alice Smith."]
        )
    assert built == [True]


async def test_schema_label_on_a_processing_document_is_refused(env):
    document_id = await _ingest(env)
    await _set_status(env, document_id, "processing")

    response = await env.client.put(f"/api/documents/{document_id}/schema", json=LABEL_PAYLOAD)

    assert response.status_code == 409
    assert await _entity_map(document_id) == JANE_ONLY
    assert await _schema_rows(env, document_id) == 0


async def test_a_claim_landing_after_the_label_gate_refuses_the_schema_write(env):
    document_id = await _ingest(env)
    real_gate = documents_module.gate_document_texts

    async def _claimed_after_gate(db, settings, document, texts, **kwargs):
        gated = await real_gate(db, settings, document, texts, **kwargs)
        await _set_status(env, document_id, "processing")
        return gated

    env.monkeypatch.setattr(documents_module, "gate_document_texts", _claimed_after_gate)

    response = await env.client.put(f"/api/documents/{document_id}/schema", json=LABEL_PAYLOAD)

    assert response.status_code == 409
    assert response.json() == NOT_EDITABLE
    assert await _schema_rows(env, document_id) == 0


async def test_schema_numeric_only_update_is_not_gated(env):
    document_id = await _ingest(env)
    await _set_status(env, document_id, "processing")

    response = await env.client.put(
        f"/api/documents/{document_id}/schema",
        json={"columns": [{"index": 0, "technical_label": "COLUMN_1", "is_numeric": True}]},
    )

    assert response.status_code == 200
    assert response.json()["columns"][0]["is_numeric"] is True
    assert await _schema_rows(env, document_id) == 1


async def test_delete_during_a_run_is_refused_and_the_run_completes(env):
    seen = {}

    async def _delete(chunk_ids, document_id):
        seen["status"] = (await env.client.delete(f"/api/chunks/{chunk_ids[0]}")).status_code

    _after_chunk_insert(env, _delete)
    document_id = await _ingest(env)

    assert seen["status"] == 409
    document = await _document(env, document_id)
    assert (document.ingestion_status, document.chunk_count) == ("completed", 2)
    assert len(await _chunks(env, document_id)) == 2


async def test_delete_keeps_the_map_and_recounts_the_chunks(env):
    document_id = await _ingest(env)
    first, second = await _chunks(env, document_id)

    assert (await env.client.delete(f"/api/chunks/{first.id}")).status_code == 204
    assert (await _document(env, document_id)).chunk_count == 1
    assert await _entity_map(document_id) == JANE_ONLY

    await _set_status(env, document_id, "completed", chunk_count=7)
    assert (await env.client.delete(f"/api/chunks/{second.id}")).status_code == 204
    document = await _document(env, document_id)
    assert (document.ingestion_status, document.chunk_count) == ("completed", 0)
    assert await _chunks(env, document_id) == []


async def test_deleting_a_chunk_twice_lowers_the_count_once(env):
    document_id = await _ingest(env)
    first, _ = await _chunks(env, document_id)

    assert (await env.client.delete(f"/api/chunks/{first.id}")).status_code == 204
    assert (await env.client.delete(f"/api/chunks/{first.id}")).status_code == 404
    assert (await _document(env, document_id)).chunk_count == 1


async def test_a_concurrent_delete_of_the_same_chunk_is_counted_once(env):
    document_id = await _ingest(env)
    first, _ = await _chunks(env, document_id)
    real_completed = chunks_module._completed_document
    inner = {}

    async def _second_delete_lands_first(db, chunk):
        document = await real_completed(db, chunk)
        if not inner:
            inner["status"] = None
            inner["status"] = (await env.client.delete(f"/api/chunks/{chunk.id}")).status_code
        return document

    env.monkeypatch.setattr(chunks_module, "_completed_document", _second_delete_lands_first)

    response = await env.client.delete(f"/api/chunks/{first.id}")

    assert (inner["status"], response.status_code) == (204, 404)
    assert (await _document(env, document_id)).chunk_count == 1
    assert len(await _chunks(env, document_id)) == 1


async def test_document_delete_locks_the_document_row_before_its_children(env):
    document_id = await _ingest(env)

    async def _no_artifacts(*args, **kwargs):
        return None

    env.monkeypatch.setattr(documents_module, "erase_document_artifacts", _no_artifacts)
    statements = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()).upper())

    event.listen(env.engine.sync_engine, "before_cursor_execute", _record)
    try:
        response = await env.client.delete(f"/api/documents/{document_id}")
    finally:
        event.remove(env.engine.sync_engine, "before_cursor_execute", _record)

    assert response.status_code == 204
    writes = [s for s in statements if s.startswith(("UPDATE DOCUMENTS", "DELETE FROM"))]
    assert writes and writes[0].startswith("UPDATE DOCUMENTS")
    assert any(s.startswith("DELETE FROM CHUNKS") for s in writes)
    assert await _document(env, document_id) is None
