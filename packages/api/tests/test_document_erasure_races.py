from __future__ import annotations

"""Document erasure against concurrent writers, FK cascades and reprocess failures.

Uses an isolated SQLite database configured by the production connect
listener (WAL, busy timeout, ``foreign_keys=ON``), so the CASCADE behaviour
under test is the one the application runs with.
"""

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from septum_core.spans import SanitizeResult
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from septum_api import database as database_module
from septum_api.database import _sqlite_wal_connect, get_db
from septum_api.main import app
from septum_api.models import Base
from septum_api.models.document import Chunk, Document
from septum_api.models.entity_detection import EntityDetection
from septum_api.models.user import User
from septum_api.routers import documents as documents_module
from septum_api.services import document_anon_store, pii_gate
from septum_api.services import document_pipeline as pipeline_module
from septum_api.services.anonymization_map import AnonymizationMap
from septum_api.utils.auth_dependency import get_current_user


class _NoopIndex:
    def delete_index(self, document_id: int) -> None:
        return None


class _Env:
    def __init__(self, session_maker: Any, client: AsyncClient, user: User, tmp: Path) -> None:
        self.session_maker = session_maker
        self.client = client
        self.user = user
        self.tmp = tmp

    async def add_document(self, status: str = "completed", detections: int = 1) -> tuple[int, int]:
        source = self.tmp / f"source-{status}-{id(object())}"
        source.write_bytes(b"ciphertext")
        async with self.session_maker() as session:
            doc = Document(
                filename=source.name,
                original_filename="report.pdf",
                file_type="application/pdf",
                file_format="pdf",
                detected_language="en",
                encrypted_path=str(source),
                file_size_bytes=10,
                ocr_confidence=0.0,
                active_regulation_ids=[],
                ingestion_status=status,
                chunk_count=1,
                user_id=self.user.id,
            )
            session.add(doc)
            await session.flush()
            chunk = Chunk(
                document_id=doc.id, index=0, sanitized_text="[PERSON_1] signed.", char_count=18
            )
            session.add(chunk)
            await session.flush()
            for _ in range(detections):
                session.add(
                    EntityDetection(
                        document_id=doc.id,
                        chunk_id=chunk.id,
                        entity_type="PERSON",
                        placeholder="[PERSON_1]",
                        start_offset=0,
                        end_offset=5,
                        score=0.9,
                    )
                )
            await session.commit()
            return doc.id, chunk.id

    async def count(self, model: Any, document_id: int) -> int:
        async with self.session_maker() as session:
            return (
                await session.execute(
                    select(func.count()).select_from(model).where(model.document_id == document_id)
                )
            ).scalar_one()

    async def document(self, document_id: int) -> Document | None:
        async with self.session_maker() as session:
            return await session.get(Document, document_id)


@pytest.fixture
async def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Env]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'erasure.db'}")
    event.listen(engine.sync_engine, "connect", _sqlite_wal_connect)
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

    monkeypatch.setattr(document_anon_store, "_ANON_MAP_DIR", tmp_path / "maps")
    monkeypatch.setattr(document_anon_store, "_document_maps", {})
    monkeypatch.setattr(document_anon_store, "_map_versions", {})
    monkeypatch.setattr(pipeline_module, "VectorStore", _NoopIndex)
    monkeypatch.setattr(pipeline_module, "BM25Retriever", _NoopIndex)
    monkeypatch.setattr(database_module, "get_session_maker", lambda: session_maker)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user] = lambda: user
        try:
            yield _Env(session_maker, client, user, tmp_path)
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)
            await engine.dispose()


async def _store_map(document_id: int) -> None:
    amap = AnonymizationMap(document_id=document_id, language="en")
    amap.add_entity("Alice Smith", "PERSON")
    await document_anon_store.set_document_map(document_id, amap)


async def test_production_engine_enforces_foreign_keys(env: _Env) -> None:
    async with env.session_maker() as session:
        assert (await session.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1


async def test_raw_document_delete_cascades_to_chunks_and_detections(env: _Env) -> None:
    doc, _ = await env.add_document(detections=2)

    async with env.session_maker() as session:
        await session.execute(text("DELETE FROM documents WHERE id = :id"), {"id": doc})
        await session.commit()

    assert await env.count(Chunk, doc) == 0
    assert await env.count(EntityDetection, doc) == 0


async def test_delete_route_removes_chunks_detections_and_map(env: _Env) -> None:
    doc, _ = await env.add_document(detections=2)
    await _store_map(doc)

    resp = await env.client.delete(f"/api/documents/{doc}")

    assert resp.status_code == 204
    assert await env.document(doc) is None
    assert await env.count(Chunk, doc) == 0
    assert await env.count(EntityDetection, doc) == 0
    assert await document_anon_store.get_document_map(doc) is None


async def test_delete_during_processing_is_refused(env: _Env) -> None:
    doc, _ = await env.add_document(status="processing")
    await _store_map(doc)

    resp = await env.client.delete(f"/api/documents/{doc}")

    assert resp.status_code == 409
    assert "processed" in resp.json()["detail"]
    assert str(env.tmp) not in resp.text
    remaining = await env.document(doc)
    assert remaining is not None and Path(remaining.encrypted_path).exists()
    assert await env.count(Chunk, doc) == 1
    assert await document_anon_store.get_document_map(doc) is not None


class _SilentSanitizer:
    """Sanitizer double that detects nothing; the gate now runs it before the lock."""

    def sanitize(self, text, language, anon_map):
        return SanitizeResult(sanitized_text=text, entity_count=0, detected_spans=[])


async def test_chunk_edit_racing_a_delete_does_not_recreate_the_map(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc, chunk_id = await env.add_document()
    await _store_map(doc)
    gate_entered = asyncio.Event()
    edit_task: list[asyncio.Task] = []

    async def _create_sanitizer(db, settings, enable_ollama=False):
        gate_entered.set()
        return _SilentSanitizer()

    real_pop = pipeline_module.pop_document_map

    async def _pop_then_start_an_edit(document_id: int) -> None:
        await real_pop(document_id)
        edit_task.append(
            asyncio.create_task(
                env.client.patch(
                    f"/api/chunks/{chunk_id}", json={"sanitized_text": "Paid by Bob Jones."}
                )
            )
        )
        await gate_entered.wait()
        await asyncio.sleep(0.1)

    monkeypatch.setattr(pii_gate, "create_sanitizer", _create_sanitizer)
    monkeypatch.setattr(pipeline_module, "pop_document_map", _pop_then_start_an_edit)

    resp = await env.client.delete(f"/api/documents/{doc}")
    edit_resp = await edit_task[0]

    assert resp.status_code == 204
    assert edit_resp.status_code == 404
    assert not (env.tmp / "maps" / f"{doc}.enc").exists()
    assert await document_anon_store.get_document_map(doc) is None


async def test_gate_aborts_when_the_document_row_is_gone(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import HTTPException

    doc, _ = await env.add_document()

    async def _create_sanitizer(db, settings, enable_ollama=False):
        return object()

    monkeypatch.setattr(pii_gate, "create_sanitizer", _create_sanitizer)
    async with env.session_maker() as session:
        document = await session.get(Document, doc)
        async with env.session_maker() as other:
            await other.execute(text("DELETE FROM documents WHERE id = :id"), {"id": doc})
            await other.commit()

        with pytest.raises(HTTPException) as excinfo:
            await pii_gate.gate_document_texts(session, object(), document, ["Bob Jones"])

    assert excinfo.value.status_code == 404
    assert not (env.tmp / "maps" / f"{doc}.enc").exists()


async def test_reprocess_with_failing_cleanup_marks_document_failed_without_path(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc, _ = await env.add_document()
    ingest_calls: list[Any] = []

    class _FailingVectorStore:
        def delete_index(self, document_id: int) -> None:
            raise PermissionError(13, "Permission denied", "/secret/vector_indexes/doc.faiss.enc")

    async def _ingest(**kwargs):
        ingest_calls.append(kwargs)
        raise AssertionError("ingestion must not start after a failed cleanup")

    monkeypatch.setattr(pipeline_module, "VectorStore", _FailingVectorStore)
    monkeypatch.setattr(documents_module._INGESTION_ROUTER, "ingest", _ingest)

    resp = await env.client.post(f"/api/documents/{doc}/reprocess")

    assert resp.status_code == 200
    stored = await env.document(doc)
    assert stored.ingestion_status == "failed"
    assert "PermissionError" in stored.ingestion_error
    assert f"document {doc}" in stored.ingestion_error
    assert "/secret" not in stored.ingestion_error
    assert ingest_calls == []


async def test_delete_after_a_concurrent_delete_returns_404(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    import contextlib

    doc, _ = await env.add_document()
    real_lock = documents_module.document_map_lock

    @contextlib.asynccontextmanager
    async def _lock_after_other_delete(document_id: int):
        async with env.session_maker() as session:
            await session.execute(text("DELETE FROM documents WHERE id = :id"), {"id": document_id})
            await session.commit()
        async with real_lock(document_id) as handle:
            yield handle

    monkeypatch.setattr(documents_module, "document_map_lock", _lock_after_other_delete)

    resp = await env.client.delete(f"/api/documents/{doc}")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Document not found."


async def test_lock_lost_on_release_after_a_committed_delete_still_succeeds(
    env: _Env, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    import contextlib

    doc, _ = await env.add_document()
    await _store_map(doc)
    real_lock = documents_module.document_map_lock

    @contextlib.asynccontextmanager
    async def _lock_lost_on_release(document_id: int):
        async with real_lock(document_id) as handle:
            yield handle
        raise document_anon_store.DocumentMapLockLost("expired")

    monkeypatch.setattr(documents_module, "document_map_lock", _lock_lost_on_release)

    with caplog.at_level("WARNING", logger=documents_module.__name__):
        resp = await env.client.delete(f"/api/documents/{doc}")

    assert resp.status_code == 204
    assert await env.document(doc) is None
    assert f"document_id={doc}" in caplog.text


async def test_reprocess_during_processing_is_refused(env: _Env) -> None:
    doc, _ = await env.add_document(status="processing")

    resp = await env.client.post(f"/api/documents/{doc}/reprocess")

    assert resp.status_code == 409
    assert "processed" in resp.json()["detail"]
    assert await env.count(Chunk, doc) == 1
    assert await env.count(EntityDetection, doc) == 1


async def test_reprocess_claim_lost_to_a_concurrent_request_is_refused(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc, _ = await env.add_document()
    real_get = documents_module.get_owned_or_404

    async def _get_then_other_claims(*args: Any, **kwargs: Any):
        document = await real_get(*args, **kwargs)
        async with env.session_maker() as session:
            await session.execute(
                text("UPDATE documents SET ingestion_status = 'processing' WHERE id = :id"),
                {"id": doc},
            )
            await session.commit()
        return document

    monkeypatch.setattr(documents_module, "get_owned_or_404", _get_then_other_claims)

    resp = await env.client.post(f"/api/documents/{doc}/reprocess")

    assert resp.status_code == 409
    assert await env.count(Chunk, doc) == 1


class _LostLock:
    """Lock handle whose ownership expired: :meth:`ensure_held` refuses."""

    async def ensure_held(self) -> None:
        raise document_anon_store.DocumentMapLockLost("expired")


def _lock_yielding(handle: Any):
    import contextlib

    @contextlib.asynccontextmanager
    async def _lock(document_id: int):
        yield handle

    return _lock


async def test_delete_aborts_when_the_lock_is_lost_before_commit(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc, _ = await env.add_document(detections=2)
    await _store_map(doc)
    real_lock = documents_module.document_map_lock
    monkeypatch.setattr(documents_module, "document_map_lock", _lock_yielding(_LostLock()))

    resp = await env.client.delete(f"/api/documents/{doc}")

    assert resp.status_code == 500
    assert "Try again" in resp.json()["detail"]
    assert await env.document(doc) is not None
    assert await env.count(Chunk, doc) == 1
    assert await env.count(EntityDetection, doc) == 2

    monkeypatch.setattr(documents_module, "document_map_lock", real_lock)
    retry = await env.client.delete(f"/api/documents/{doc}")
    assert retry.status_code == 204
    assert await env.document(doc) is None


async def test_gate_refuses_to_write_the_map_under_a_lost_lock(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc, _ = await env.add_document()
    writes: list[int] = []

    async def _create_sanitizer(db, settings, enable_ollama=False):
        return object()

    async def _gate(texts, working, db, settings, sanitizer=None):
        working.add_entity("Bob Jones", "PERSON")
        return list(texts)

    async def _record_write(document_id: int, anon_map: AnonymizationMap) -> None:
        writes.append(document_id)

    monkeypatch.setattr(pii_gate, "create_sanitizer", _create_sanitizer)
    monkeypatch.setattr(pii_gate, "gate_untrusted_texts", _gate)
    monkeypatch.setattr(pii_gate, "set_document_map", _record_write)
    monkeypatch.setattr(pii_gate, "document_map_lock", _lock_yielding(_LostLock()))

    async with env.session_maker() as session:
        document = await session.get(Document, doc)
        with pytest.raises(document_anon_store.DocumentMapLockLost):
            await pii_gate.gate_document_texts(session, object(), document, ["Bob Jones"])

    assert writes == []
    assert not (env.tmp / "maps" / f"{doc}.enc").exists()


async def test_chunk_edit_over_an_unreadable_map_fails_without_overwriting_it(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc, chunk_id = await env.add_document()
    await _store_map(doc)
    map_file = document_anon_store._map_path(doc)
    corrupted = b"not an encrypted anonymization map"
    map_file.write_bytes(corrupted)
    document_anon_store._forget(doc)

    async def _create_sanitizer(db, settings, enable_ollama=False):
        return object()

    monkeypatch.setattr(pii_gate, "create_sanitizer", _create_sanitizer)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            f"/api/chunks/{chunk_id}", json={"sanitized_text": "Paid by Bob Jones."}
        )

    assert resp.status_code >= 500
    assert "Bob Jones" not in resp.text
    assert map_file.read_bytes() == corrupted


def test_every_map_write_is_guarded_by_a_held_lock() -> None:
    """Each ``set_document_map`` call sits in a ``document_map_lock`` block,
    directly after ``ensure_held`` on that block's handle, so a writer whose
    lock expired can never ``os.replace`` a map back after an erasure."""
    import ast

    import septum_api

    package_root = Path(septum_api.__file__).parent
    found = 0
    for source in package_root.rglob("*.py"):
        if source.name == "document_anon_store.py":
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "set_document_map"
            ):
                continue
            found += 1
            stmt = parents[parents[node]]
            container = parents[stmt]
            stmts = next(
                body for body in (getattr(container, f, None) for f in ("body", "orelse"))
                if isinstance(body, list) and stmt in body
            )
            index = stmts.index(stmt)
            assert index > 0, f"{source.name}: map write without ensure_held"
            prev = stmts[index - 1]
            receiver = getattr(getattr(getattr(prev, "value", None), "value", None), "func", None)
            assert (
                isinstance(receiver, ast.Attribute) and receiver.attr == "ensure_held"
            ), f"{source.name}: map write not preceded by ensure_held"
            handles: set[str] = set()
            walker = stmt
            while walker in parents:
                walker = parents[walker]
                if isinstance(walker, ast.AsyncWith):
                    handles |= {
                        item.optional_vars.id
                        for item in walker.items
                        if isinstance(item.context_expr, ast.Call)
                        and getattr(item.context_expr.func, "id", None) == "document_map_lock"
                        and isinstance(item.optional_vars, ast.Name)
                    }
            assert getattr(receiver.value, "id", None) in handles, (
                f"{source.name}: ensure_held is not called on an enclosing document_map_lock"
            )
    assert found >= 2


async def test_ingestion_failure_stores_no_exception_text(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from septum_api.models.error_log import ErrorLog

    sentinel = "Sentinel Patient Q7X9 diagnosed"
    doc, _ = await env.add_document()

    async def _ingest(**kwargs):
        raise ValueError(f"could not parse page: {sentinel}")

    monkeypatch.setattr(documents_module._INGESTION_ROUTER, "ingest", _ingest)

    resp = await env.client.post(f"/api/documents/{doc}/reprocess")

    assert resp.status_code == 200
    stored = await env.document(doc)
    assert stored.ingestion_status == "failed"
    assert stored.ingestion_error == "Ingestion failed (ValueError)"
    async with env.session_maker() as session:
        rows = (await session.execute(select(ErrorLog))).scalars().all()
    assert rows, "the failure still reaches the error log"
    for row in rows:
        assert sentinel not in row.message
        assert sentinel not in (row.stack_trace or "")
        assert sentinel not in str(row.extra)
    assert any(row.exception_type == "ValueError" and " in _ingest" in row.stack_trace for row in rows)
