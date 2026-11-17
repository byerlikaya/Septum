"""The anonymization map against a real Redis: a writer whose cross-worker lock lapsed.

The Redis map lock expires after ``_LOCK_TIMEOUT_SECONDS``; a writer stalled
between :meth:`DocumentMapLockHandle.ensure_held` and its write (a suspended
host, a hung disk) can lose it to an eraser in another worker. What keeps
the map from outliving the erasure is the database: every map writer holds
the document row from before it reads the map until its write is committed,
and every eraser takes the row before it erases. Each test expires the lock
right before the write, lets the eraser of "another worker" (fresh
process-local locks) run, and checks that no map tier is left behind.

These tests run only when ``SEPTUM_TEST_REDIS_URL`` points at a throwaway
Redis database (it is flushed before each test); the redis-py lock needs
Lua, which fakeredis lacks here. Each runs on SQLite and, when
``SEPTUM_TEST_POSTGRES_URL`` is set, on PostgreSQL.
"""

from __future__ import annotations

import asyncio
import os
import weakref
from datetime import timedelta
from types import SimpleNamespace

import pytest
from map_tier_db import POSTGRES_URL, map_tier_db
from redis_doubles import use_redis
from septum_api.services.document_anon_store import DocumentMapLockLost
from test_document_pipeline import (
    _claim_clearing_the_rows,
    _lease_clock,
    _lease_sweep,
    _seed_derived,
    _status_token_error,
)

REDIS_URL = os.getenv("SEPTUM_TEST_REDIS_URL", "")
# SQLite: how long the eraser gets to finish while the writer is stalled; it
# only finishes in that time when nothing holds it back. PostgreSQL shows
# the eraser waiting on the row lock instead, so no grace is needed there;
# this bounds the wait for that sign.
ERASER_GRACE_SECONDS = 1.0
LOCK_WAIT_SIGN_SECONDS = 10.0

pytestmark = pytest.mark.skipif(not REDIS_URL, reason="SEPTUM_TEST_REDIS_URL not set")


@pytest.fixture
async def redis_probe(monkeypatch):
    """The app's Redis client pointed at ``SEPTUM_TEST_REDIS_URL``, plus a probe client on it."""
    import redis.asyncio as aioredis
    import septum_api.services.redis_client as redis_module

    use_redis(monkeypatch, REDIS_URL)
    probe = aioredis.from_url(REDIS_URL)
    await probe.flushdb()
    yield probe
    if redis_module._redis_pool is not None:
        await redis_module._redis_pool.aclose()
    await probe.aclose()


@pytest.fixture(params=["sqlite", "postgresql"])
async def db(request, tmp_path, monkeypatch, redis_probe):
    if request.param == "postgresql" and not POSTGRES_URL:
        pytest.skip("SEPTUM_TEST_POSTGRES_URL not set")
    monkeypatch.setenv("VECTOR_INDEX_DIR", str(tmp_path / "faiss"))
    monkeypatch.setenv("BM25_INDEX_DIR", str(tmp_path / "bm25"))
    async with map_tier_db(request.param, tmp_path) as store:
        yield store


@pytest.fixture
def maker(db):
    return db.maker


async def _tiers(probe, document_id: int) -> dict:
    import septum_api.services.document_anon_store as store

    version = await probe.get(f"anon_map_version:{document_id}")
    return {
        "disk": store._map_path(document_id).exists(),
        "redis_map": await probe.exists(f"anon_map:{document_id}") == 1,
        "version": version.decode() if version else None,
    }


_ERASED = {"disk": False, "redis_map": False, "version": "erased"}


class _OtherWorker:
    """Runs an eraser as another worker would, once the writer's Redis lock lapsed."""

    def __init__(self, monkeypatch, probe, db) -> None:
        from septum_api.routers import documents
        from septum_api.services import document_pipeline as dp

        self._monkeypatch = monkeypatch
        self._probe = probe
        self._db = db
        self.erased = asyncio.Event()
        self.task: asyncio.Task | None = None
        real = dp.erase_document_artifacts

        async def _erase_and_signal(*args, **kwargs):
            await real(*args, **kwargs)
            self.erased.set()

        monkeypatch.setattr(dp, "erase_document_artifacts", _erase_and_signal)
        monkeypatch.setattr(documents, "erase_document_artifacts", _erase_and_signal)

    async def run_while_stalled(self, document_id: int, eraser) -> bool:
        """Expire the writer's lock, start ``eraser``; return whether it erased within the grace."""
        import septum_api.services.document_anon_store as store

        await self._probe.delete(f"anon_map_lock:{document_id}")
        self._monkeypatch.setattr(store, "_map_write_locks", weakref.WeakValueDictionary())
        self.task = asyncio.create_task(eraser())
        if self._db.dialect == "postgresql":
            return await self._erased_unless_waiting_on_a_lock()
        try:
            await asyncio.wait_for(self.erased.wait(), timeout=ERASER_GRACE_SECONDS)
        except TimeoutError:
            return False
        return True

    async def _erased_unless_waiting_on_a_lock(self) -> bool:
        """PostgreSQL: ``True`` once the eraser erased, ``False`` once it is seen waiting on a lock."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + LOCK_WAIT_SIGN_SECONDS
        while loop.time() < deadline:
            if self.erased.is_set():
                return True
            if await self._db.waiting_on_a_lock("UPDATE documents"):
                return False
            await asyncio.sleep(0.02)
        raise AssertionError("the eraser neither erased nor waited on a lock")


def _stall_before_write(monkeypatch, module, worker: _OtherWorker, document_id: int, eraser):
    """Run ``eraser`` in another worker right before ``module``'s map write; record whether it got through."""
    real_set = module.set_document_map
    seen: dict = {}

    async def _stalled_set(doc_id, anon_map):
        seen["erased_first"] = await worker.run_while_stalled(document_id, eraser)
        await real_set(doc_id, anon_map)

    monkeypatch.setattr(module, "set_document_map", _stalled_set)
    return seen


def _gate_adding(monkeypatch, name: str) -> None:
    """Replace detection: every gated text adds ``name`` to the map."""
    from septum_api.services import pii_gate

    async def _gate(texts, anon_map, db, settings, **kwargs):
        anon_map.entity_map[name] = "[PERSON_NAME_9]"
        return list(texts)

    monkeypatch.setattr(pii_gate, "gate_untrusted_texts", _gate)


async def test_a_run_whose_map_lock_lapsed_stores_before_the_lease_sweep_erases(
    tmp_path, monkeypatch, db, maker, redis_probe
) -> None:
    from septum_api.services import document_pipeline as dp
    from septum_api.services import ingestion_lease
    from septum_api.services.anonymization_map import AnonymizationMap

    clock = _lease_clock(monkeypatch)
    document_id, token = await _claim_clearing_the_rows(maker, tmp_path)
    worker = _OtherWorker(monkeypatch, redis_probe, db)

    async def _sweep() -> None:
        clock.now += timedelta(seconds=ingestion_lease.LEASE_SECONDS + 1)
        await _lease_sweep(maker)

    seen = _stall_before_write(monkeypatch, dp, worker, document_id, _sweep)
    new_map = AnonymizationMap(document_id=document_id, language="en")
    new_map.entity_map["Bob"] = "[PERSON_NAME_1]"
    async with maker() as session:
        with pytest.raises(DocumentMapLockLost):
            await dp._store_document_map(session, document_id, new_map, run_token=token)
    await worker.task

    assert seen["erased_first"] is False
    assert (await _status_token_error(maker, document_id))[0] == "failed"
    assert await _tiers(redis_probe, document_id) == _ERASED


async def test_an_edit_whose_map_lock_lapsed_leaves_no_map_for_a_deleted_document(
    tmp_path, monkeypatch, db, maker, redis_probe
) -> None:
    from septum_api.models.document import Document
    from septum_api.routers import documents
    from septum_api.services import pii_gate

    document_id = await _seed_derived(maker, tmp_path, "completed")
    worker = _OtherWorker(monkeypatch, redis_probe, db)

    async def _delete() -> None:
        async with maker() as other:
            assert await documents.erase_document(other, await other.get(Document, document_id))

    _gate_adding(monkeypatch, "Bob")
    seen = _stall_before_write(monkeypatch, pii_gate, worker, document_id, _delete)
    async with maker() as session:
        document = await session.get(Document, document_id)
        with pytest.raises(DocumentMapLockLost):
            await pii_gate.gate_document_texts(
                session, SimpleNamespace(), document, ["Bob x"],
                sanitizer=SimpleNamespace(context_gate=False),
            )
    await worker.task

    assert seen["erased_first"] is False
    async with maker() as session:
        assert await session.get(Document, document_id) is None
    assert await _tiers(redis_probe, document_id) == _ERASED


@pytest.mark.parametrize("eraser", ["failed_purge", "reprocess_cleanup"])
async def test_a_remask_whose_map_lock_lapsed_leaves_no_map_behind_an_erasure(
    tmp_path, monkeypatch, db, maker, redis_probe, eraser
) -> None:
    from septum_api.models.document import Document
    from septum_api.services import document_pipeline as dp
    from septum_api.services import pii_gate

    if eraser == "failed_purge":
        document_id, token = await _seed_derived(maker, tmp_path, "failed"), None
    else:
        document_id, token = await _claim_clearing_the_rows(maker, tmp_path)
    worker = _OtherWorker(monkeypatch, redis_probe, db)

    async def _erase() -> None:
        async with maker() as other:
            if eraser == "failed_purge":
                await dp.purge_failed_derived_data(other, document_id)
            else:
                await dp.DocumentPipeline.cleanup_artifacts(document_id, db=other, run_token=token)

    _gate_adding(monkeypatch, "Bob")
    seen = _stall_before_write(monkeypatch, pii_gate, worker, document_id, _erase)
    async with maker() as session:
        document = await session.get(Document, document_id)
        with pytest.raises(DocumentMapLockLost):
            await pii_gate.remask_document_texts(
                session, SimpleNamespace(), document, ["Bob x"],
                sanitizer=SimpleNamespace(context_gate=False),
            )
    await worker.task

    assert seen["erased_first"] is False
    assert await _tiers(redis_probe, document_id) == _ERASED


async def test_a_label_edit_whose_map_lock_lapsed_leaves_no_map_for_a_deleted_document(
    tmp_path, monkeypatch, db, maker, redis_probe
) -> None:
    from septum_api.models.document import Document
    from septum_api.routers import documents
    from septum_api.services import pii_gate
    from septum_api.utils.db_helpers import load_settings

    document_id = await _seed_derived(maker, tmp_path, "completed")
    async with maker() as session:
        await load_settings(session)
    worker = _OtherWorker(monkeypatch, redis_probe, db)

    async def _delete() -> None:
        async with maker() as other:
            assert await documents.erase_document(other, await other.get(Document, document_id))

    _gate_adding(monkeypatch, "Bob")
    seen = _stall_before_write(monkeypatch, pii_gate, worker, document_id, _delete)
    payload = SimpleNamespace(columns=[SimpleNamespace(semantic_label="Bob x")])
    async with maker() as session:
        document = await session.get(Document, document_id)
        with pytest.raises(DocumentMapLockLost):
            await documents._gate_semantic_labels(session, document, payload)
    await worker.task

    assert seen["erased_first"] is False
    async with maker() as session:
        assert await session.get(Document, document_id) is None
    assert await _tiers(redis_probe, document_id) == _ERASED


async def test_a_chunk_delete_whose_map_lock_lapsed_leaves_nothing_for_a_deleted_document(
    tmp_path, monkeypatch, db, maker, redis_probe
) -> None:
    """The chunk delete only rewrites index files, and only ones that still exist."""
    from fastapi import HTTPException
    from septum_api.models.document import Chunk, Document
    from septum_api.routers import chunks, documents
    from septum_api.services.document_anon_store import DocumentMapLockHandle
    from sqlalchemy import select
    from test_document_pipeline import _index_paths

    document_id = await _seed_derived(maker, tmp_path, "completed")
    async with maker() as session:
        chunk_id = await session.scalar(select(Chunk.id).where(Chunk.document_id == document_id))
    worker = _OtherWorker(monkeypatch, redis_probe, db)

    async def _delete() -> None:
        async with maker() as other:
            assert await documents.erase_document(other, await other.get(Document, document_id))

    real_ensure = DocumentMapLockHandle.ensure_held
    stalled: list = []

    async def _ensure_then_stall(handle) -> None:
        await real_ensure(handle)
        if not stalled:
            stalled.append(None)
            stalled[0] = await worker.run_while_stalled(document_id, _delete)

    monkeypatch.setattr(DocumentMapLockHandle, "ensure_held", _ensure_then_stall)
    async with maker() as session:
        with pytest.raises(HTTPException):
            await chunks._drop_from_indexes(session, document_id, None, [chunk_id])
    await worker.task

    assert stalled == [True]
    assert [path.exists() for path in _index_paths(tmp_path, document_id)] == [False, False]
    assert await _tiers(redis_probe, document_id) == _ERASED
