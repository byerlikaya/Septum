"""Every map writer and eraser that holds the document row ends its transaction, on success and on failure.

A writer holds the row (a no-op UPDATE) across the map write; an eraser
holds it across the erasure. A path that left that transaction open would
keep every other writer of the row (a claim, the lease sweep, an edit) and,
on SQLite, of the whole database waiting. Each test runs the path in a
session that stays open, then checks that a fresh connection can still
write the row. Runs on SQLite and, when ``SEPTUM_TEST_POSTGRES_URL`` is
set, on PostgreSQL; no Redis is involved.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from map_tier_db import POSTGRES_URL, map_tier_db
from redis_doubles import redis_unavailable
from test_document_pipeline import _claim_clearing_the_rows, _seed_derived


@pytest.fixture(params=["sqlite", "postgresql"])
async def db(request, tmp_path, monkeypatch):
    if request.param == "postgresql" and not POSTGRES_URL:
        pytest.skip("SEPTUM_TEST_POSTGRES_URL not set")
    monkeypatch.setenv("VECTOR_INDEX_DIR", str(tmp_path / "faiss"))
    monkeypatch.setenv("BM25_INDEX_DIR", str(tmp_path / "bm25"))
    async with map_tier_db(request.param, tmp_path) as store:
        yield store


def _gate_adding(monkeypatch, name: str) -> None:
    from septum_api.services import pii_gate

    async def _gate(texts, anon_map, db, settings, **kwargs):
        anon_map.entity_map[name] = "[PERSON_NAME_9]"
        return list(texts)

    monkeypatch.setattr(pii_gate, "gate_untrusted_texts", _gate)


async def _edit(session, document_id: int) -> None:
    from septum_api.models.document import Document
    from septum_api.services import pii_gate

    document = await session.get(Document, document_id)
    await pii_gate.gate_document_texts(
        session, SimpleNamespace(), document, ["Bob x"],
        sanitizer=SimpleNamespace(context_gate=False),
    )


async def test_a_committed_map_write_releases_the_row(tmp_path, monkeypatch, db) -> None:
    from septum_api.services.document_anon_store import load_persisted_document_map

    document_id = await _seed_derived(db.maker, tmp_path, "completed")
    _gate_adding(monkeypatch, "Bob")
    async with db.maker() as session:
        await _edit(session, document_id)
        assert await db.row_writable(document_id)
    assert "Bob" in (await load_persisted_document_map(document_id)).entity_map


async def test_a_map_write_that_fails_inside_the_fence_releases_the_row(
    tmp_path, monkeypatch, db
) -> None:
    from septum_api.services import pii_gate

    document_id = await _seed_derived(db.maker, tmp_path, "completed")
    _gate_adding(monkeypatch, "Bob")

    async def _disk_full(document_id, anon_map):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(pii_gate, "set_document_map", _disk_full)
    async with db.maker() as session:
        with pytest.raises(OSError):
            await _edit(session, document_id)
        assert await db.row_writable(document_id)


async def test_a_fence_refused_under_the_lock_releases_the_row(tmp_path, monkeypatch, db) -> None:
    """The document stopped being editable between the check and the fence; the refusal ends the transaction."""
    from septum_api.services import pii_gate

    document_id = await _seed_derived(db.maker, tmp_path, "failed")
    _gate_adding(monkeypatch, "Bob")

    async def _passes(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(pii_gate, "_require_row", _passes)
    async with db.maker() as session:
        with pytest.raises(pii_gate.DocumentNotEditableError):
            await _edit(session, document_id)
        assert await db.row_writable(document_id)


async def test_a_delete_whose_erasure_fails_releases_the_row(tmp_path, monkeypatch, db) -> None:
    from fastapi import HTTPException
    from septum_api.models.document import Document
    from septum_api.routers import documents
    from septum_api.services.document_pipeline import ArtifactErasureError

    document_id = await _seed_derived(db.maker, tmp_path, "completed")

    async def _erasure_fails(document_id, **kwargs) -> None:
        raise ArtifactErasureError("vector_index", PermissionError(13, "denied"))

    monkeypatch.setattr(documents, "erase_document_artifacts", _erasure_fails)
    async with db.maker() as session:
        document = await session.get(Document, document_id)
        with pytest.raises(HTTPException) as excinfo:
            await documents.erase_document(session, document)
        assert excinfo.value.status_code == 500
        assert await db.row_writable(document_id)


async def test_the_reprocess_cleanup_releases_the_row(tmp_path, db) -> None:
    from septum_api.services.document_pipeline import DocumentPipeline

    document_id, token = await _claim_clearing_the_rows(db.maker, tmp_path)
    async with db.maker() as session:
        await DocumentPipeline.cleanup_artifacts(document_id, db=session, run_token=token)
        assert await db.row_writable(document_id)


async def test_the_failed_purge_erasure_releases_the_row(tmp_path, db) -> None:
    from septum_api.services import document_pipeline as dp

    document_id = await _seed_derived(db.maker, tmp_path, "failed")
    async with db.maker() as session:
        assert await dp._erase_failed_artifacts(session, document_id, clear_reply_cache=False)
        assert await db.row_writable(document_id)


async def _store_known(document_id: int, name: str = "Alice") -> None:
    from septum_api.services.anonymization_map import AnonymizationMap
    from septum_api.services.document_anon_store import set_document_map

    anon_map = AnonymizationMap(document_id=document_id, language="en")
    anon_map.add_entity(name, "PERSON_NAME")
    await set_document_map(document_id, anon_map)


async def test_a_remask_that_adds_nothing_takes_no_row_lock(tmp_path, monkeypatch, db) -> None:
    """Only a re-mask that writes the map holds the row; a turn that adds nothing issues no UPDATE."""
    from septum_api.models.document import Document
    from septum_api.services import pii_gate
    from sqlalchemy import event

    document_id = await _seed_derived(db.maker, tmp_path, "completed")
    await _store_known(document_id)

    async def _gate_nothing(texts, anon_map, db, settings, **kwargs):
        return list(texts)

    monkeypatch.setattr(pii_gate, "gate_untrusted_texts", _gate_nothing)
    updates: list[str] = []

    def _record(conn, cursor, statement, *args):
        if statement.lstrip().upper().startswith("UPDATE"):
            updates.append(statement)

    async with db.maker() as session:
        engine = session.bind.sync_engine
        event.listen(engine, "before_cursor_execute", _record)
        try:
            document = await session.get(Document, document_id)
            _, written = await pii_gate.remask_document_texts(
                session, SimpleNamespace(), document, ["nothing new"],
                sanitizer=SimpleNamespace(context_gate=False),
            )
        finally:
            event.remove(engine, "before_cursor_execute", _record)
    assert written is False
    assert updates == []


async def test_a_map_moved_before_the_row_is_held_is_merged_not_overwritten(
    tmp_path, monkeypatch, db
) -> None:
    """Another writer stores a newer map between the compare under the lock and the row fence."""
    import contextlib

    from septum_api.services import pii_gate
    from septum_api.services.anonymization_map import AnonymizationMap
    from septum_api.services.document_anon_store import (
        load_persisted_document_map,
        set_document_map,
    )

    document_id = await _seed_derived(db.maker, tmp_path, "completed")
    await _store_known(document_id)
    _gate_adding(monkeypatch, "Bob")
    real_row_held = pii_gate._row_held
    raced: list = []

    @contextlib.asynccontextmanager
    async def _another_writer_first(session, doc_id, **kwargs):
        if not raced:
            raced.append(True)
            newer = AnonymizationMap(document_id=doc_id, language="en")
            newer.entity_map.update({"Alice": "[PERSON_NAME_1]", "Carol": "[PERSON_NAME_2]"})
            await set_document_map(doc_id, newer)
        async with real_row_held(session, doc_id, **kwargs):
            yield

    monkeypatch.setattr(pii_gate, "_row_held", _another_writer_first)
    async with db.maker() as session:
        await _edit(session, document_id)

    stored = (await load_persisted_document_map(document_id)).entity_map
    assert raced == [True]
    assert {"Alice", "Carol", "Bob"} <= set(stored)


async def test_two_remasks_of_different_documents_both_write_on_sqlite(
    tmp_path, monkeypatch, db
) -> None:
    """A re-mask whose row check read before another document's map write committed still takes its fence.

    SQLite in WAL mode refuses to turn a read transaction into a write one
    once another connection committed after the read began
    (SQLITE_BUSY_SNAPSHOT, no busy wait). The check before the fence is a
    plain SELECT that leaves no read transaction open, so the fence starts
    a fresh write transaction and waits like any writer.
    """
    import asyncio
    import contextlib

    from septum_api.models.document import Document
    from septum_api.services import pii_gate
    from septum_api.services.document_anon_store import load_persisted_document_map

    if db.dialect != "sqlite":
        pytest.skip("SQLite snapshot behaviour")
    first = await _seed_derived(db.maker, tmp_path, "completed")
    second = await _seed_derived(db.maker, tmp_path, "completed")
    for document_id in (first, second):
        await _store_known(document_id)

    async def _gate(texts, anon_map, db, settings, **kwargs):
        anon_map.entity_map[f"Bob{anon_map.document_id}"] = "[PERSON_NAME_9]"
        return list(texts)

    monkeypatch.setattr(pii_gate, "gate_untrusted_texts", _gate)
    first_committed = asyncio.Event()
    second_checked = asyncio.Event()
    real_moved = pii_gate._map_moved

    async def _second_waits_for_the_first(document_id, base, required):
        if document_id == second and not second_checked.is_set():
            second_checked.set()
            await first_committed.wait()
        return await real_moved(document_id, base, required)

    monkeypatch.setattr(pii_gate, "_map_moved", _second_waits_for_the_first)
    real_row_held = pii_gate._row_held
    open_reads: list[bool] = []

    @contextlib.asynccontextmanager
    async def _no_read_open_before_the_fence(session, document_id, **kwargs):
        raw = (await session.connection()).sync_connection.connection.dbapi_connection
        open_reads.append(raw._connection._conn.in_transaction)
        async with real_row_held(session, document_id, **kwargs):
            yield

    monkeypatch.setattr(pii_gate, "_row_held", _no_read_open_before_the_fence)

    async def _remask(document_id: int) -> bool:
        async with db.maker() as session:
            document = await session.get(Document, document_id)
            _, written = await pii_gate.remask_document_texts(
                session, SimpleNamespace(), document, ["x"],
                sanitizer=SimpleNamespace(context_gate=False),
            )
        return written

    async def _first_after_the_second_read() -> bool:
        await second_checked.wait()
        written = await _remask(first)
        first_committed.set()
        return written

    assert await asyncio.gather(_remask(second), _first_after_the_second_read()) == [True, True]
    assert open_reads == [False, False]
    for document_id in (first, second):
        assert f"Bob{document_id}" in (await load_persisted_document_map(document_id)).entity_map


async def test_a_map_store_releases_the_row_when_it_commits_and_when_it_fails(
    tmp_path, monkeypatch, db
) -> None:
    from septum_api.services import document_pipeline as dp
    from septum_api.services.anonymization_map import AnonymizationMap

    document_id, token = await _claim_clearing_the_rows(db.maker, tmp_path)
    anon_map = AnonymizationMap(document_id=document_id, language="en")
    async with db.maker() as session:
        await dp._store_document_map(session, document_id, anon_map, run_token=token)
        assert await db.row_writable(document_id)

    async def _disk_full(document_id, anon_map):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(dp, "set_document_map", _disk_full)
    async with db.maker() as session:
        with pytest.raises(OSError):
            await dp._store_document_map(session, document_id, anon_map, run_token=token)
        assert await db.row_writable(document_id)


async def test_a_delete_whose_erasure_raises_unexpectedly_releases_the_row(
    tmp_path, monkeypatch, db
) -> None:
    from septum_api.models.document import Document
    from septum_api.routers import documents

    document_id = await _seed_derived(db.maker, tmp_path, "completed")

    async def _erasure_breaks(document_id, **kwargs) -> None:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(documents, "erase_document_artifacts", _erasure_breaks)
    async with db.maker() as session:
        document = await session.get(Document, document_id)
        with pytest.raises(RuntimeError):
            await documents.erase_document(session, document)
        assert await db.row_writable(document_id)


def _redis_down(monkeypatch) -> None:
    """A configured Redis inside its retry window: no client, but configured."""
    redis_unavailable(monkeypatch, "redis://127.0.0.1:1/0")


async def test_with_redis_down_only_a_remask_that_adds_nothing_goes_through(
    tmp_path, monkeypatch, db
) -> None:
    from septum_api.models.document import Document
    from septum_api.services import pii_gate
    from septum_api.services.document_anon_store import DocumentMapLockTimeout

    document_id = await _seed_derived(db.maker, tmp_path, "completed")
    await _store_known(document_id)
    _redis_down(monkeypatch)
    adds: list[str] = []

    async def _gate(texts, anon_map, db, settings, **kwargs):
        for name in adds:
            anon_map.add_entity(name, "PERSON_NAME")
        return list(texts)

    monkeypatch.setattr(pii_gate, "gate_untrusted_texts", _gate)

    async def _remask() -> bool:
        async with db.maker() as session:
            document = await session.get(Document, document_id)
            _, written = await pii_gate.remask_document_texts(
                session, SimpleNamespace(), document, ["nothing new"],
                sanitizer=SimpleNamespace(context_gate=False),
            )
        return written

    assert await _remask() is False
    adds.append("Bob")
    with pytest.raises(DocumentMapLockTimeout):
        await _remask()
    adds.clear()
    async with db.maker() as session:
        with pytest.raises(DocumentMapLockTimeout):
            await _edit(session, document_id)
