"""User deletion erases every stored copy of the user's documents; the startup sweep erases leftovers."""

from __future__ import annotations

import contextlib
import logging
import os
import time
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from septum_api.database import _sqlite_wal_connect, get_db
from septum_api.main import app
from septum_api.models import Base
from septum_api.models.document import Document
from septum_api.models.user import User
from septum_api.routers import documents as documents_module
from septum_api.routers import users as users_module
from septum_api.services import document_anon_store, orphan_artifacts, source_files
from septum_api.services.anonymization_map import AnonymizationMap
from septum_api.services.bm25_retriever import BM25Retriever
from septum_api.services.document_anon_store import (
    DocumentMapLockLost,
    DocumentMapLockTimeout,
)
from septum_api.services.document_pipeline import ArtifactErasureError
from septum_api.services.vector_store import VectorStore
from septum_api.utils.auth_dependency import get_current_user
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

RAW_NAME = "Alice Example"


class _World:
    def __init__(self, session_maker: Any, client: AsyncClient, tmp: Path) -> None:
        self.session_maker = session_maker
        self.client = client
        self.tmp = tmp
        self.sources = tmp / "sources"
        self.sources.mkdir()
        self.acting: User | None = None

    async def add_user(self, email: str, role: str = "editor", is_active: bool = True) -> User:
        async with self.session_maker() as session:
            user = User(email=email, hashed_password="x", role=role, is_active=is_active)
            session.add(user)
            await session.commit()
            return user

    async def add_document(self, user: User, status: str = "completed") -> Document:
        source = self.sources / uuid4().hex
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
                user_id=user.id,
            )
            session.add(doc)
            await session.commit()
        await write_artifacts(doc.id)
        return doc

    async def row(self, model: Any, row_id: int) -> Any:
        async with self.session_maker() as session:
            return await session.get(model, row_id)


async def write_artifacts(document_id: int) -> None:
    """Store an index pair and a map for ``document_id`` the way the pipeline does."""
    VectorStore()._index_path(document_id).write_bytes(b"ciphertext")
    BM25Retriever()._index_path(document_id).write_bytes(b"ciphertext")
    amap = AnonymizationMap(document_id=document_id, language="en")
    amap.entity_map[RAW_NAME] = "[PERSON_1]"
    await document_anon_store.set_document_map(document_id, amap)


async def stored_tiers(document_id: int, redis_client: Any = None) -> set[str]:
    """Return the names of the tiers that still hold a copy of the document."""
    tiers = set()
    if VectorStore()._index_path(document_id).exists():
        tiers.add("faiss")
    if BM25Retriever()._index_path(document_id).exists():
        tiers.add("bm25")
    if (document_anon_store._ANON_MAP_DIR / f"{document_id}.enc").exists():
        tiers.add("map_disk")
    if redis_client is not None and await redis_client.exists(f"anon_map:{document_id}"):
        tiers.add("map_redis")
    return tiers


ALL_DISK_TIERS = {"faiss", "bm25", "map_disk"}


def _all_tiers(redis_client: Any) -> set[str]:
    return ALL_DISK_TIERS | ({"map_redis"} if redis_client is not None else set())


class _HeldLock:
    """Always-granted Redis lock: fakeredis cannot run the Lua scripts of a real one."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    async def acquire(self) -> bool:
        return True

    async def release(self) -> None:
        return None

    async def extend(self, *_args: Any, **_kwargs: Any) -> bool:
        return True


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch):
    """A configured, reachable Redis backed by fakeredis."""
    import fakeredis
    import septum_api.services.redis_client as redis_module

    client = fakeredis.FakeAsyncRedis()
    monkeypatch.setattr(client, "lock", _HeldLock, raising=False)
    monkeypatch.setattr(redis_module, "_get_redis_url", lambda: "redis://fake")
    monkeypatch.setattr(redis_module, "_redis_pool", client)
    monkeypatch.setattr(redis_module, "_redis_unavailable", False)
    return client


@pytest.fixture(params=[False, True], ids=["disk", "redis"])
def redis_client(request: pytest.FixtureRequest):
    """No Redis, or a fakeredis-backed one; tests run against both map backends."""
    return request.getfixturevalue("fake_redis") if request.param else None


@pytest.fixture
async def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_World]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'users.db'}")
    event.listen(engine.sync_engine, "connect", _sqlite_wal_connect)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _override_get_db():
        async with session_maker() as session:
            yield session

    monkeypatch.setenv("VECTOR_INDEX_DIR", str(tmp_path / "faiss"))
    monkeypatch.setenv("BM25_INDEX_DIR", str(tmp_path / "bm25"))
    monkeypatch.setattr(document_anon_store, "_ANON_MAP_DIR", tmp_path / "maps")
    monkeypatch.setattr(document_anon_store, "_document_maps", {})
    monkeypatch.setattr(document_anon_store, "_map_versions", {})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        state = _World(session_maker, client, tmp_path)
        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user] = lambda: state.acting
        try:
            yield state
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)
            await engine.dispose()


async def _admin_and_target(world: _World) -> tuple[User, User]:
    world.acting = await world.add_user("admin@example.com", role="admin")
    target = await world.add_user("target@example.com")
    return world.acting, target


async def test_delete_user_erases_every_document_artifact(world: _World, redis_client) -> None:
    admin, target = await _admin_and_target(world)
    owned = [await world.add_document(target), await world.add_document(target, "failed")]
    kept = await world.add_document(admin)

    resp = await world.client.delete(f"/api/users/{target.id}")

    assert resp.status_code == 204, resp.text
    assert await world.row(User, target.id) is None
    for doc in owned:
        assert await world.row(Document, doc.id) is None
        assert not Path(doc.encrypted_path).exists()
        assert await stored_tiers(doc.id, redis_client) == set()
    expected = ALL_DISK_TIERS | ({"map_redis"} if redis_client is not None else set())
    assert await stored_tiers(kept.id, redis_client) == expected
    assert Path(kept.encrypted_path).exists()
    assert await world.row(Document, kept.id) is not None


async def test_processing_document_refuses_and_erases_nothing(world: _World, redis_client) -> None:
    _, target = await _admin_and_target(world)
    done = await world.add_document(target)
    busy = await world.add_document(target, "processing")

    resp = await world.client.delete(f"/api/users/{target.id}")

    assert resp.status_code == 409
    assert await world.row(User, target.id) is not None
    for doc in (done, busy):
        assert await world.row(Document, doc.id) is not None
        assert Path(doc.encrypted_path).exists()
        assert await stored_tiers(doc.id, redis_client) == _all_tiers(redis_client)


async def test_failed_erasure_keeps_user_and_remaining_documents_retryable(
    world: _World, monkeypatch: pytest.MonkeyPatch, redis_client
) -> None:
    _, target = await _admin_and_target(world)
    first = await world.add_document(target)
    second = await world.add_document(target)
    real_erase = documents_module.erase_document_artifacts

    async def _failing_on_second(document_id: int, **kwargs: Any) -> None:
        if document_id == second.id:
            raise ArtifactErasureError("vector_index", PermissionError(13, "denied", "/secret/path"))
        await real_erase(document_id, **kwargs)

    monkeypatch.setattr(documents_module, "erase_document_artifacts", _failing_on_second)

    resp = await world.client.delete(f"/api/users/{target.id}")

    assert resp.status_code == 500
    assert "/secret/path" not in resp.text
    assert RAW_NAME not in resp.text
    assert await world.row(User, target.id) is not None
    assert await world.row(Document, first.id) is None
    assert await stored_tiers(first.id, redis_client) == set()
    assert await world.row(Document, second.id) is not None
    assert await stored_tiers(second.id, redis_client) == _all_tiers(redis_client)

    monkeypatch.setattr(documents_module, "erase_document_artifacts", real_erase)
    retry = await world.client.delete(f"/api/users/{target.id}")

    assert retry.status_code == 204, retry.text
    assert await world.row(User, target.id) is None
    assert await stored_tiers(second.id, redis_client) == set()


async def test_document_added_during_deletion_refuses(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, target = await _admin_and_target(world)
    await world.add_document(target)
    real_erase = users_module.erase_document
    added: list[Document] = []

    async def _upload_meanwhile(db: Any, document: Document) -> bool:
        erased = await real_erase(db, document)
        if not added:
            added.append(await world.add_document(target, "processing"))
        return erased

    monkeypatch.setattr(users_module, "erase_document", _upload_meanwhile)

    resp = await world.client.delete(f"/api/users/{target.id}")

    assert resp.status_code == 409
    assert await world.row(User, target.id) is not None
    assert await world.row(Document, added[0].id) is not None
    assert await stored_tiers(added[0].id) == ALL_DISK_TIERS


async def test_self_delete_is_refused_before_any_erasure(world: _World) -> None:
    admin, _ = await _admin_and_target(world)
    doc = await world.add_document(admin)

    resp = await world.client.delete(f"/api/users/{admin.id}")

    assert resp.status_code == 409
    assert await stored_tiers(doc.id) == ALL_DISK_TIERS


async def test_last_active_admin_is_refused_before_any_erasure(world: _World) -> None:
    world.acting = await world.add_user("inactive@example.com", role="admin", is_active=False)
    last_admin = await world.add_user("last@example.com", role="admin")
    doc = await world.add_document(last_admin)

    resp = await world.client.delete(f"/api/users/{last_admin.id}")

    assert resp.status_code == 409
    assert "last active administrator" in resp.text
    assert await world.row(User, last_admin.id) is not None
    assert await stored_tiers(doc.id) == ALL_DISK_TIERS


def _stub_lock(failure: str):
    """A map lock that times out on entry (``"timeout"``) or is found lost before a write (``"lost"``)."""

    class _Handle:
        async def ensure_held(self) -> None:
            raise DocumentMapLockLost("lost")

    @contextlib.asynccontextmanager
    async def _lock(_document_id: int, **_kwargs: Any):
        if failure == "timeout":
            raise DocumentMapLockTimeout("busy")
        yield _Handle()

    return _lock


def _unlink_failing_for(suffix: str):
    real_unlink = Path.unlink

    def _unlink(self: Path, missing_ok: bool = False) -> None:
        if self.name.endswith(suffix):
            raise PermissionError(13, "Permission denied", str(self))
        real_unlink(self, missing_ok=missing_ok)

    return _unlink


def _unreachable_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    import septum_api.services.redis_client as redis_module

    monkeypatch.setattr(redis_module, "_get_redis_url", lambda: "redis://unreachable")
    monkeypatch.setattr(redis_module, "_redis_unavailable", True)


@pytest.mark.parametrize(
    ("failure", "artifacts_kept"), [("timeout", True), ("lost", False)], ids=["timeout", "lost"]
)
async def test_lock_failure_is_a_500_like_the_document_route(
    world: _World, monkeypatch: pytest.MonkeyPatch, failure: str, artifacts_kept: bool
) -> None:
    _, target = await _admin_and_target(world)
    doc = await world.add_document(target)
    other = await world.add_document(target)
    monkeypatch.setattr(documents_module, "document_map_lock", _stub_lock(failure))

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        by_user = await client.delete(f"/api/users/{target.id}")
        by_document = await client.delete(f"/api/documents/{other.id}")

    assert by_user.status_code == by_document.status_code == 500
    assert by_user.json() == by_document.json()
    assert await world.row(User, target.id) is not None
    assert await world.row(Document, doc.id) is not None
    # A lost lock is detected right before the commit, after the erasure ran.
    assert (await stored_tiers(doc.id) == ALL_DISK_TIERS) is artifacts_kept


async def test_real_unlink_failure_returns_500_without_paths(
    world: _World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _, target = await _admin_and_target(world)
    doc = await world.add_document(target)
    monkeypatch.setattr(Path, "unlink", _unlink_failing_for(".bm25.enc"))

    with caplog.at_level(logging.INFO):
        resp = await world.client.delete(f"/api/users/{target.id}")

    assert resp.status_code == 500
    assert str(world.tmp) not in resp.text
    assert str(world.tmp) not in caplog.text
    assert f"document_id={doc.id} tier=bm25_index error=PermissionError" in caplog.text
    assert await world.row(User, target.id) is not None
    assert await world.row(Document, doc.id) is not None


async def test_unreachable_map_cache_returns_500_and_keeps_the_user(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, target = await _admin_and_target(world)
    doc = await world.add_document(target)
    _unreachable_redis(monkeypatch)

    resp = await world.client.delete(f"/api/users/{target.id}")

    assert resp.status_code == 500
    assert await world.row(User, target.id) is not None
    assert await world.row(Document, doc.id) is not None


async def test_document_inserted_right_before_the_user_delete_refuses(world: _World) -> None:
    import sqlite3

    _, target = await _admin_and_target(world)
    engine = world.session_maker.kw["bind"]
    inserted: list[int] = []

    def _upload_lands(conn, cursor, statement: str, *_args: Any) -> None:
        if inserted or not statement.lstrip().upper().startswith("DELETE FROM USERS"):
            return
        with contextlib.closing(sqlite3.connect(world.tmp / "users.db")) as other:
            row = other.execute(
                "INSERT INTO documents (user_id, filename, original_filename, file_type, "
                "file_format, detected_language, uploaded_at, encrypted_path, chunk_count, "
                "entity_count, ingestion_status, file_size_bytes, active_regulation_ids) "
                "VALUES (?, 'f', 'f.pdf', 'application/pdf', 'pdf', 'en', "
                "'2026-01-01 00:00:00', '/nowhere/f', 0, 0, 'processing', 1, '[]')",
                (target.id,),
            )
            other.commit()
            inserted.append(row.lastrowid)

    event.listen(engine.sync_engine, "before_cursor_execute", _upload_lands)
    try:
        resp = await world.client.delete(f"/api/users/{target.id}")
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _upload_lands)

    assert inserted
    assert resp.status_code == 409
    assert await world.row(User, target.id) is not None
    assert await world.row(Document, inserted[0]) is not None


def _age(path: Path) -> None:
    old = time.time() - orphan_artifacts.SOURCE_MIN_AGE_SECONDS - 60
    os.utime(path, (old, old))


async def _sweep(world: _World) -> orphan_artifacts.OrphanSweepResult:
    return await orphan_artifacts.sweep_orphan_artifacts(world.session_maker, world.sources)


async def _live_document(world: _World) -> Document:
    """A document with all its artifacts, so the sweep's empty-table guard does not apply."""
    owner = await world.add_user(f"owner-{uuid4().hex}@example.com")
    live = await world.add_document(owner)
    _age(Path(live.encrypted_path))
    return live


async def test_sweep_erases_only_orphans(world: _World, redis_client) -> None:
    live = await _live_document(world)
    orphan_id = live.id + 100
    await write_artifacts(orphan_id)
    old_orphan = world.sources / uuid4().hex
    old_orphan.write_bytes(b"ciphertext")
    _age(old_orphan)
    young_orphan = world.sources / uuid4().hex
    young_orphan.write_bytes(b"ciphertext")
    unrelated = world.sources / "README"
    unrelated.write_bytes(b"x")
    _age(unrelated)

    result = await _sweep(world)

    assert (result.documents, result.source_files, result.failures) == (1, 1, 0)
    assert await stored_tiers(orphan_id, redis_client) == set()
    expected = ALL_DISK_TIERS | ({"map_redis"} if redis_client is not None else set())
    assert await stored_tiers(live.id, redis_client) == expected
    assert Path(live.encrypted_path).exists()
    assert not old_orphan.exists()
    assert young_orphan.exists()
    assert unrelated.exists()


async def test_sweep_erases_nothing_when_the_documents_table_is_empty(
    world: _World, caplog: pytest.LogCaptureFixture
) -> None:
    await write_artifacts(3)
    orphan = world.sources / uuid4().hex
    orphan.write_bytes(b"ciphertext")
    _age(orphan)

    with caplog.at_level(logging.ERROR, logger=orphan_artifacts.__name__):
        result = await _sweep(world)

    assert (result.documents, result.source_files, result.held_back) == (0, 0, 2)
    assert await stored_tiers(3) == ALL_DISK_TIERS
    assert orphan.exists()
    assert "documents table is empty but 1 stored document ids and 1 source files" in caplog.text
    assert str(world.tmp) not in caplog.text


async def test_sweep_skips_a_document_created_before_it_takes_the_lock(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    live = await _live_document(world)
    orphan_id = 42
    await write_artifacts(orphan_id)
    real_lock = orphan_artifacts.document_map_lock

    @contextlib.asynccontextmanager
    async def _row_appears_first(document_id: int, **kwargs: Any):
        async with world.session_maker() as session:
            session.add(
                Document(
                    id=document_id,
                    filename="x",
                    original_filename="x.pdf",
                    file_type="application/pdf",
                    file_format="pdf",
                    detected_language="en",
                    encrypted_path=str(world.sources / "x"),
                    file_size_bytes=1,
                    ingestion_status="processing",
                    user_id=live.user_id,
                )
            )
            await session.commit()
        async with real_lock(document_id, **kwargs) as lock:
            yield lock

    monkeypatch.setattr(orphan_artifacts, "document_map_lock", _row_appears_first)

    result = await _sweep(world)

    assert (result.documents, result.failures) == (0, 0)
    assert await stored_tiers(orphan_id) == ALL_DISK_TIERS


@pytest.mark.parametrize(
    ("failure", "error"),
    [
        ("timeout", "DocumentMapLockTimeout"),
        ("lost", "DocumentMapLockLost"),
    ],
    ids=["timeout", "lost"],
)
async def test_sweep_erases_nothing_without_the_lock(
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
    error: str,
) -> None:
    await _live_document(world)
    await write_artifacts(9)
    monkeypatch.setattr(orphan_artifacts, "document_map_lock", _stub_lock(failure))

    with caplog.at_level(logging.ERROR, logger=orphan_artifacts.__name__):
        result = await _sweep(world)

    assert (result.documents, result.failures) == (0, 1)
    assert await stored_tiers(9) == ALL_DISK_TIERS
    assert f"document_id=9 tier=map_lock error={error}" in caplog.text


async def test_sweep_logs_a_real_unlink_failure_without_paths_and_continues(
    world: _World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    await _live_document(world)
    await write_artifacts(7)
    orphan = world.sources / uuid4().hex
    orphan.write_bytes(b"ciphertext")
    _age(orphan)
    monkeypatch.setattr(Path, "unlink", _unlink_failing_for(".bm25.enc"))

    with caplog.at_level(logging.INFO):
        result = await _sweep(world)

    assert (result.documents, result.source_files, result.failures) == (0, 1, 1)
    assert BM25Retriever()._index_path(7).exists()
    assert not orphan.exists()
    assert "document_id=7 tier=bm25_index error=PermissionError" in caplog.text
    assert str(world.tmp) not in caplog.text
    assert RAW_NAME not in caplog.text


async def test_sweep_with_an_unreachable_map_cache(
    world: _World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    live = await _live_document(world)
    await write_artifacts(5)
    _unreachable_redis(monkeypatch)

    with caplog.at_level(logging.ERROR):
        result = await _sweep(world)

    # One failure for the unreadable cache listing, one for orphan 5's map,
    # which pop_document_map cannot confirm erased from the cache tier.
    assert (result.documents, result.source_files, result.failures) == (0, 0, 2)
    assert "tier=anonymization_map_cache" in caplog.text
    assert await stored_tiers(5) == set()
    assert await stored_tiers(live.id) == ALL_DISK_TIERS


async def test_oversized_or_foreign_ids_are_skipped(
    world: _World, fake_redis: Any
) -> None:
    await _live_document(world)
    await fake_redis.set("anon_map:" + "9" * 5000, b"x")
    await fake_redis.set("anon_map:not-a-number", b"x")
    await fake_redis.set("anon_map:99999999999", b"x")
    await fake_redis.set("anon_map:007", b"x")
    (VectorStore().base_dir / f"doc_{'9' * 30}.faiss.enc").write_bytes(b"x")
    (VectorStore().base_dir / "doc_0.faiss.enc").write_bytes(b"x")
    (VectorStore().base_dir / "doc_007.faiss.enc").write_bytes(b"x")

    assert await document_anon_store.redis_map_document_ids() == {1}
    assert VectorStore().stored_document_ids() == {1}
    result = await _sweep(world)

    assert result.failures == 0
    assert await fake_redis.exists("anon_map:" + "9" * 5000)


async def test_sweep_finds_nothing_on_an_empty_store(world: _World) -> None:
    result = await _sweep(world)

    assert (result.documents, result.source_files, result.failures, result.held_back) == (0, 0, 0, 0)
    async with world.session_maker() as session:
        assert (await session.scalars(select(Document))).all() == []


@pytest.fixture
def started_lifespan(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Run the lifespan's configured branch with every startup step stubbed and recorded."""
    from septum_api import main

    calls: list[Any] = []

    async def _record(name: str) -> None:
        calls.append(name)

    sentinel = object()
    monkeypatch.setattr(main.bootstrap, "needs_setup", lambda: False)
    monkeypatch.setattr(main, "initialize_engine", lambda _url: calls.append("initialize_engine"))
    monkeypatch.setattr(main, "init_db", lambda: _record("init_db"))
    monkeypatch.setattr(main, "_reset_orphaned_processing_docs", lambda: _record("reset"))
    monkeypatch.setattr(main, "sweep_stale_temp_files", lambda: calls.append("temp_sweep"))
    monkeypatch.setattr(main, "get_session_maker", lambda: sentinel)
    monkeypatch.setattr(main, "_announce_setup_token", lambda: _record("announce"))
    monkeypatch.setattr(main, "_error_log_retention_loop", lambda: _record("retention"))
    monkeypatch.setenv(main.MODEL_WARMUP_OPT_OUT_ENV, "1")
    calls.append(sentinel)
    return calls


async def test_lifespan_sweeps_the_real_storage_dir_after_init_db(
    started_lifespan: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from septum_api import main

    sentinel = started_lifespan.pop(0)

    async def _sweep_stub(session_maker: Any, source_dir: Path) -> None:
        started_lifespan.append(("sweep", session_maker, source_dir))

    monkeypatch.setattr(main, "sweep_orphan_artifacts", _sweep_stub)

    async with main.lifespan(main.app):
        pass

    sweep = next(c for c in started_lifespan if isinstance(c, tuple))
    assert sweep == ("sweep", sentinel, source_files.SOURCE_FILE_DIR)
    assert started_lifespan.index("init_db") < started_lifespan.index(sweep)


async def test_lifespan_survives_a_failing_sweep(
    started_lifespan: list[Any], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from septum_api import main
    from sqlalchemy.exc import OperationalError

    async def _broken_sweep(*_args: Any) -> None:
        raise OperationalError("SELECT documents.id FROM /secret/db", {}, Exception("locked"))

    monkeypatch.setattr(main, "sweep_orphan_artifacts", _broken_sweep)

    with caplog.at_level(logging.ERROR):
        async with main.lifespan(main.app):
            started_lifespan.append("serving")

    assert "serving" in started_lifespan
    assert "Orphaned artifact sweep failed: OperationalError" in caplog.text
    assert "/secret/db" not in caplog.text


async def test_user_removed_during_the_erasure_returns_404(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, target = await _admin_and_target(world)
    await world.add_document(target)
    real_erase = users_module.erase_document

    async def _removed_meanwhile(db: Any, document: Document) -> bool:
        erased = await real_erase(db, document)
        async with world.session_maker() as session:
            await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": target.id})
            await session.commit()
        return erased

    monkeypatch.setattr(users_module, "erase_document", _removed_meanwhile)

    resp = await world.client.delete(f"/api/users/{target.id}")

    assert resp.status_code == 404


async def test_last_admin_rule_is_checked_again_after_the_erasure(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.acting = await world.add_user("admin@example.com", role="admin")
    target = await world.add_user("other-admin@example.com", role="admin")
    await world.add_document(target)
    real_erase = users_module.erase_document

    async def _acting_admin_deactivated(db: Any, document: Document) -> bool:
        erased = await real_erase(db, document)
        async with world.session_maker() as session:
            await session.execute(
                text("UPDATE users SET is_active = 0 WHERE id = :id"), {"id": world.acting.id}
            )
            await session.commit()
        return erased

    monkeypatch.setattr(users_module, "erase_document", _acting_admin_deactivated)

    resp = await world.client.delete(f"/api/users/{target.id}")

    assert resp.status_code == 409
    assert "last active administrator" in resp.text
    assert await world.row(User, target.id) is not None


_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


async def test_upload_whose_row_cannot_be_committed_leaves_no_source_file(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = world.tmp / "uploads"
    storage.mkdir()
    monkeypatch.setattr(documents_module, "_DOC_STORAGE_DIR", storage)
    # The uploader was deleted meanwhile: the row's user foreign key fails.
    world.acting = User(id=9999, email="gone@example.com", hashed_password="x", role="editor")

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/documents/upload", files={"file": ("scan.png", _PNG, "image/png")}
        )

    assert resp.status_code == 500
    assert list(storage.iterdir()) == []
    async with world.session_maker() as session:
        assert (await session.scalars(select(Document))).all() == []


async def test_sweep_waits_only_briefly_for_a_map_lock(
    world: _World, fake_redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _live_document(world)
    await write_artifacts(77)
    waits: list[Any] = []

    class _RecordingLock(_HeldLock):
        def __init__(self, *_args: Any, **kwargs: Any) -> None:
            waits.append(kwargs.get("blocking_timeout"))

    monkeypatch.setattr(fake_redis, "lock", _RecordingLock, raising=False)

    result = await _sweep(world)

    assert result.documents == 1
    assert waits == [orphan_artifacts.SWEEP_LOCK_WAIT_SECONDS]
    assert orphan_artifacts.SWEEP_LOCK_WAIT_SECONDS < document_anon_store._LOCK_WAIT_SECONDS


async def test_sweep_keeps_a_file_referenced_only_by_its_stored_path(world: _World) -> None:
    owner = await world.add_user("owner@example.com")
    stored = world.sources / uuid4().hex
    stored.write_bytes(b"ciphertext")
    _age(stored)
    async with world.session_maker() as session:
        session.add(
            Document(
                filename="legacy-name",
                original_filename="old.pdf",
                file_type="application/pdf",
                file_format="pdf",
                detected_language="en",
                encrypted_path=str(stored),
                file_size_bytes=10,
                ingestion_status="completed",
                user_id=owner.id,
            )
        )
        await session.commit()
    hex_dir = world.sources / uuid4().hex
    hex_dir.mkdir()
    os.utime(hex_dir, (time.time() - 3600, time.time() - 3600))

    result = await _sweep(world)

    assert (result.source_files, result.failures) == (0, 0)
    assert stored.exists()
    assert hex_dir.is_dir()


async def test_sweep_skips_a_source_file_that_vanishes_while_listed(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _live_document(world)
    vanishing = world.sources / uuid4().hex
    vanishing.write_bytes(b"ciphertext")
    orphan = world.sources / uuid4().hex
    orphan.write_bytes(b"ciphertext")
    _age(vanishing)
    _age(orphan)
    real_stat = Path.stat

    def _stat(self: Path, *args: Any, **kwargs: Any):
        if self.name == vanishing.name:
            raise FileNotFoundError(2, "No such file", str(self))
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", _stat)

    result = await _sweep(world)

    assert (result.source_files, result.failures) == (1, 0)
    assert not orphan.exists()
