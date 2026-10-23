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
from septum_api.services import document_anon_store
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
