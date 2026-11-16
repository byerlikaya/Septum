"""The ingestion lease on PostgreSQL: claim, renewal, expiry sweep and failed-document purge.

The lease column is ``timestamptz`` there, and every lease is written and
compared with the app's UTC clock as a bound parameter. These tests run
only when ``SEPTUM_TEST_POSTGRES_URL`` points at a throwaway database
(``postgresql+asyncpg``); each gets a schema of its own, with a session
time zone that is not UTC, so a lease stored or compared in local time
would show. Races are forced on row locks (a commit held open, the waiting
statement seen in ``pg_stat_activity``), never with sleeps.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, AsyncIterator

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

POSTGRES_URL = os.getenv("SEPTUM_TEST_POSTGRES_URL", "")
SESSION_TIME_ZONE = "Asia/Kolkata"
T0 = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
SECRET_TEXT = "Alice Quorvexdaltine"

pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="SEPTUM_TEST_POSTGRES_URL not set")


@pytest.fixture(autouse=True)
def _empty_registry():
    from septum_api.services import ingestion_lease

    ingestion_lease._held.clear()
    yield
    ingestion_lease._held.clear()


@pytest.fixture
def clock(monkeypatch):
    """A settable lease clock, starting at ``T0``."""
    from septum_api.services import ingestion_lease

    state = SimpleNamespace(now=T0)
    monkeypatch.setattr(ingestion_lease, "lease_now", lambda: state.now)
    return state


@pytest.fixture
async def pg() -> AsyncIterator[SimpleNamespace]:
    """A fresh schema on the test database; yields its session maker and an admin engine."""
    from septum_api.models import Base

    schema = f"lease_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(POSTGRES_URL)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        POSTGRES_URL,
        connect_args={"server_settings": {"search_path": schema, "TimeZone": SESSION_TIME_ZONE}},
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield SimpleNamespace(
            maker=async_sessionmaker(bind=engine, expire_on_commit=False), admin=admin, schema=schema
        )
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


def _lease(seconds: int = 0) -> timedelta:
    from septum_api.services import ingestion_lease

    return timedelta(seconds=ingestion_lease.LEASE_SECONDS + seconds)


async def _add_document(maker, status: str = "completed", **values: Any) -> int:
    from septum_api.models.document import Document

    async with maker() as session:
        doc = Document(
            filename="f", original_filename="f", file_type="x", file_format="pdf",
            detected_language="en", encrypted_path="/x", file_size_bytes=1,
            ingestion_status=status, **values,
        )
        session.add(doc)
        await session.commit()
        return doc.id


async def _add_derived(maker, document_id: int) -> None:
    """Give a document a chunk, a detection, extracted text, a map and both index files."""
    from septum_api.models.document import Chunk, Document
    from septum_api.models.entity_detection import EntityDetection
    from septum_api.services.anonymization_map import AnonymizationMap
    from septum_api.services.bm25_retriever import BM25Retriever
    from septum_api.services.document_anon_store import set_document_map
    from septum_api.services.vector_store import VectorStore

    async with maker() as session:
        await session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(transcription_text=SECRET_TEXT, chunk_count=1, entity_count=1)
        )
        chunk = Chunk(
            document_id=document_id, index=0, sanitized_text="[PERSON_NAME_1] x",
            raw_text=SECRET_TEXT, char_count=len(SECRET_TEXT),
        )
        session.add(chunk)
        await session.flush()
        session.add(EntityDetection(
            document_id=document_id, chunk_id=chunk.id, entity_type="PERSON_NAME",
            placeholder="[PERSON_NAME_1]", start_offset=0, end_offset=5, score=1.0,
        ))
        await session.commit()
    await set_document_map(document_id, AnonymizationMap(document_id=document_id, language="en"))
    BM25Retriever().index_document(document_id, [1], [SECRET_TEXT])
    VectorStore()._index_path(document_id).write_bytes(b"index")


async def _strip_rows(maker, document_id: int) -> None:
    """Leave only the stored map and index files, as a reingest claim whose cleanup failed does."""
    from septum_api.models.document import Chunk, Document
    from septum_api.models.entity_detection import EntityDetection
    from septum_api.services.document_pipeline import DERIVED_DATA_RESET

    async with maker() as session:
        await session.execute(delete(EntityDetection).where(EntityDetection.document_id == document_id))
        await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
        await session.execute(
            update(Document).where(Document.id == document_id).values(**DERIVED_DATA_RESET)
        )
        await session.commit()


async def _derived(maker, document_id: int) -> dict:
    from septum_api.models.document import Chunk, Document
    from septum_api.models.entity_detection import EntityDetection
    from septum_api.services.bm25_retriever import BM25Retriever
    from septum_api.services.document_anon_store import get_document_map
    from septum_api.services.vector_store import VectorStore

    async with maker() as session:
        doc = await session.get(Document, document_id)
        rows = [
            await session.scalar(
                select(func.count()).select_from(model).where(model.document_id == document_id)
            )
            for model in (Chunk, EntityDetection)
        ]
    return {
        "status": doc.ingestion_status,
        "rows": rows,
        "text": doc.transcription_text,
        "map": await get_document_map(document_id) is not None,
        "faiss": VectorStore()._index_path(document_id).exists(),
        "bm25": BM25Retriever()._index_path(document_id).exists(),
    }


PURGED = {"status": "failed", "rows": [0, 0], "text": None, "map": False, "faiss": False, "bm25": False}


def _untouched(status: str) -> dict:
    return {"status": status, "rows": [1, 1], "text": SECRET_TEXT, "map": True, "faiss": True, "bm25": True}


async def _claim(maker, document_id: int) -> str:
    from septum_api.routers import documents

    async with maker() as session:
        token = await documents.claim_for_reingest(session, document_id)
    assert token
    return token


async def _row(maker, document_id: int) -> tuple:
    from septum_api.models.document import Document

    async with maker() as session:
        row = await session.get(Document, document_id)
        return row.ingestion_status, row.ingestion_run_id, row.ingestion_error


async def _lease_equals(maker, document_id: int, moment: datetime) -> bool:
    """Whether the stored lease equals ``moment``, compared in SQL with a bound value."""
    from septum_api.models.document import Document

    async with maker() as session:
        return (
            await session.scalar(
                select(Document.id).where(
                    Document.id == document_id, Document.ingestion_lease_expires_at == moment
                )
            )
        ) is not None


# ---------------------------------------------------------------- the stored lease


async def test_a_claim_stores_an_aware_lease_that_compares_with_the_app_clock(pg) -> None:
    from septum_api.models.document import Document
    from septum_api.services import ingestion_lease

    async with pg.admin.connect() as conn:
        column_type = await conn.scalar(
            text(
                "SELECT data_type FROM information_schema.columns WHERE table_schema = :schema"
                " AND table_name = 'documents' AND column_name = 'ingestion_lease_expires_at'"
            ),
            {"schema": pg.schema},
        )
    assert column_type == "timestamp with time zone"

    document_id = await _add_document(pg.maker)
    before = ingestion_lease.lease_now()
    await _claim(pg.maker, document_id)
    after = ingestion_lease.lease_now()

    async with pg.maker() as session:
        stored = await session.scalar(
            select(Document.ingestion_lease_expires_at).where(Document.id == document_id)
        )
        live = await session.scalar(
            select(Document.id).where(
                Document.id == document_id,
                Document.ingestion_lease_expires_at > ingestion_lease.lease_now(),
            )
        )
        beyond = await session.scalar(
            select(Document.id).where(
                Document.id == document_id,
                Document.ingestion_lease_expires_at > after + _lease(1),
            )
        )
    assert stored.utcoffset() is not None
    assert before + _lease() <= stored <= after + _lease()
    assert stored > ingestion_lease.lease_now()
    assert live == document_id
    assert beyond is None


# ---------------------------------------------------------------- expiry sweep


async def test_the_sweep_fails_lapsed_and_leaseless_runs_spares_live_ones_and_erases_derived_data(
    pg, clock
) -> None:
    from septum_api.services import ingestion_lease

    leaseless = await _add_document(pg.maker, "processing", ingestion_run_id="run-a")
    lapsed = await _add_document(
        pg.maker, "pending", ingestion_run_id="run-b",
        ingestion_lease_expires_at=T0 - timedelta(microseconds=1),
    )
    live = await _add_document(
        pg.maker, "processing", ingestion_run_id="run-c", ingestion_lease_expires_at=T0 + _lease()
    )
    at_the_clock = await _add_document(
        pg.maker, "processing", ingestion_run_id="run-d", ingestion_lease_expires_at=T0
    )
    completed = await _add_document(pg.maker, "completed")
    for document_id in (leaseless, live, completed):
        await _add_derived(pg.maker, document_id)

    failed = await ingestion_lease.fail_expired_ingestions(pg.maker)

    assert sorted(failed) == [leaseless, lapsed]
    error = ingestion_lease.INTERRUPTED_ERROR
    assert await _row(pg.maker, leaseless) == ("failed", "run-a", error)
    assert await _row(pg.maker, lapsed) == ("failed", "run-b", error)
    assert await _row(pg.maker, live) == ("processing", "run-c", None)
    assert await _row(pg.maker, at_the_clock) == ("processing", "run-d", None)
    assert await _derived(pg.maker, leaseless) == PURGED
    assert await _derived(pg.maker, live) == _untouched("processing")
    assert await _derived(pg.maker, completed) == _untouched("completed")
    assert await ingestion_lease.fail_expired_ingestions(pg.maker) == []


# ---------------------------------------------------------------- renewal


async def test_renewal_extends_a_held_run_and_is_refused_after_a_newer_claim(pg, clock) -> None:
    from septum_api.models.document import Document
    from septum_api.services import ingestion_lease

    document_id = await _add_document(pg.maker)
    old_token = await _claim(pg.maker, document_id)
    clock.now = T0 + timedelta(seconds=60)
    with ingestion_lease.held(document_id, old_token):
        await ingestion_lease.renew_all(pg.maker)
        assert ingestion_lease._held == {(document_id, old_token)}
    assert await _lease_equals(pg.maker, document_id, clock.now + _lease())

    async with pg.maker() as session:
        await session.execute(
            update(Document).where(Document.id == document_id).values(ingestion_status="failed")
        )
        await session.commit()
    clock.now = T0 + timedelta(seconds=70)
    new_token = await _claim(pg.maker, document_id)
    clock.now = T0 + timedelta(seconds=100)

    with ingestion_lease.held(document_id, old_token), ingestion_lease.held(document_id, new_token):
        await ingestion_lease.renew_all(pg.maker)
        assert ingestion_lease._held == {(document_id, new_token)}

    assert await _lease_equals(pg.maker, document_id, clock.now + _lease())
    assert await _row(pg.maker, document_id) == ("processing", new_token, None)


async def test_renewal_is_refused_once_the_sweep_failed_the_row(pg, clock) -> None:
    from septum_api.services import ingestion_lease

    document_id = await _add_document(pg.maker)
    token = await _claim(pg.maker, document_id)
    clock.now = T0 + _lease(1)

    assert await ingestion_lease.fail_expired_ingestions(pg.maker) == [document_id]
    with ingestion_lease.held(document_id, token):
        await ingestion_lease.renew_all(pg.maker)
        assert ingestion_lease._held == set()

    assert await _row(pg.maker, document_id) == ("failed", token, ingestion_lease.INTERRUPTED_ERROR)
    assert await _lease_equals(pg.maker, document_id, T0 + _lease())


# ---------------------------------------------------------------- races on the row lock


def _gated(maker, *, commit: bool):
    """A session maker whose sessions hold their commit until ``gate`` opens.

    ``written`` is set once a session reached its commit (its UPDATE holds the
    row lock). With ``commit=False`` the commit raises instead, and the
    session closes with a rollback.
    """
    state = SimpleNamespace(gate=asyncio.Event(), written=asyncio.Event())

    def _open():
        session = maker()
        real_commit = session.commit

        async def _commit() -> None:
            state.written.set()
            await state.gate.wait()
            if not commit:
                raise RuntimeError("commit refused")
            await real_commit()

        session.commit = _commit
        return session

    state.open = _open
    return state


async def _until_waiting_on_a_lock(pg, task: asyncio.Task) -> None:
    """Return once the sweep's UPDATE waits on a row lock in PostgreSQL.

    Each poll ends its transaction: ``pg_stat_activity`` is a snapshot taken
    once per transaction.
    """
    query = text(
        "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'"
        " AND datname = current_database()"
        " AND query LIKE 'UPDATE documents SET ingestion_status%'"
    )
    async with pg.admin.connect() as conn:
        for _ in range(1000):
            waiting = await conn.scalar(query)
            await conn.rollback()
            if waiting:
                return
            assert not task.done(), "the sweep finished without waiting on the row lock"
            await asyncio.sleep(0.01)
    raise AssertionError("the sweep never waited on the row lock")


async def _sweep_behind(pg, gated, write) -> list[int]:
    """Hold ``write`` at its commit, sweep, release the commit once the sweep waits; return the sweep's ids."""
    from septum_api.services import ingestion_lease

    writer = asyncio.create_task(write)
    sweep = None
    try:
        await asyncio.wait_for(gated.written.wait(), timeout=10)
        sweep = asyncio.create_task(ingestion_lease.fail_expired_ingestions(pg.maker))
        await _until_waiting_on_a_lock(pg, sweep)
        gated.gate.set()
        await asyncio.wait_for(writer, timeout=10)
        return await asyncio.wait_for(sweep, timeout=10)
    finally:
        gated.gate.set()
        for task in (writer, sweep):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(t for t in (writer, sweep) if t is not None), return_exceptions=True)


async def _renew_through(gated, document_id: int, token: str) -> None:
    from septum_api.services import ingestion_lease

    with ingestion_lease.held(document_id, token):
        await ingestion_lease.renew_all(gated.open)


async def _claim_through(gated, document_id: int) -> None:
    from septum_api.routers import documents

    async with gated.open() as session:
        await documents.claim_for_reingest(session, document_id)


@pytest.mark.parametrize("writer", ["renewal", "claim"])
async def test_a_write_committed_while_the_sweep_waits_on_its_row_is_honoured(
    pg, clock, writer: str
) -> None:
    """READ COMMITTED: the waiting UPDATE re-evaluates its condition on the committed row."""
    if writer == "renewal":
        document_id = await _add_document(pg.maker)
        token = await _claim(pg.maker, document_id)
    else:
        document_id = await _add_document(
            pg.maker, "pending", ingestion_lease_expires_at=T0 - timedelta(seconds=1)
        )
    clock.now = T0 + _lease(60)
    gated = _gated(pg.maker, commit=True)
    if writer == "renewal":
        write = _renew_through(gated, document_id, token)
    else:
        write = _claim_through(gated, document_id)

    assert await _sweep_behind(pg, gated, write) == []
    status, run_token, error = await _row(pg.maker, document_id)
    assert (status, error) == ("processing", None)
    if writer == "renewal":
        assert run_token == token
    assert await _lease_equals(pg.maker, document_id, clock.now + _lease())


async def test_a_renewal_rolled_back_while_the_sweep_waits_lets_the_sweep_fail_the_row(
    pg, clock
) -> None:
    from septum_api.services import ingestion_lease

    document_id = await _add_document(pg.maker)
    token = await _claim(pg.maker, document_id)
    clock.now = T0 + _lease(60)
    gated = _gated(pg.maker, commit=False)

    assert await _sweep_behind(pg, gated, _renew_through(gated, document_id, token)) == [document_id]
    assert await _row(pg.maker, document_id) == ("failed", token, ingestion_lease.INTERRUPTED_ERROR)
    assert await _lease_equals(pg.maker, document_id, T0 + _lease())


# ---------------------------------------------------------------- failed-document purge


async def test_the_purge_erases_a_failed_document_that_kept_only_its_files(pg) -> None:
    from septum_api.services import document_pipeline as dp

    leftover = await _add_document(pg.maker, "failed", ingestion_error="boom")
    completed = await _add_document(pg.maker, "completed")
    for document_id in (leftover, completed):
        await _add_derived(pg.maker, document_id)
        await _strip_rows(pg.maker, document_id)

    assert await dp._failed_ids_with_derived_data(pg.maker) == [leftover]
    assert await dp.purge_failed_documents(pg.maker) == 1

    assert await _derived(pg.maker, leftover) == PURGED
    assert await dp._failed_ids_with_derived_data(pg.maker) == []
    assert await dp.purge_failed_documents(pg.maker) == 0
    kept = await _derived(pg.maker, completed)
    assert (kept["map"], kept["faiss"], kept["bm25"]) == (True, True, True)


async def test_a_failing_index_tier_still_erases_the_map_and_keeps_the_rows_for_the_retry(
    pg, monkeypatch
) -> None:
    from septum_api.services import document_pipeline as dp
    from septum_api.services.vector_store import VectorStore

    document_id = await _add_document(pg.maker, "failed", ingestion_error="boom")
    await _add_derived(pg.maker, document_id)

    def _stuck(self, doc_id: int) -> None:
        raise PermissionError(13, "denied")

    with monkeypatch.context() as patch:
        patch.setattr(VectorStore, "delete_index", _stuck)
        assert await dp.purge_failed_documents(pg.maker) == 0

    stuck = await _derived(pg.maker, document_id)
    assert (stuck["map"], stuck["bm25"], stuck["faiss"]) == (False, False, True)
    assert (stuck["status"], stuck["rows"], stuck["text"]) == ("failed", [1, 1], SECRET_TEXT)

    assert await dp.purge_failed_documents(pg.maker) == 1
    assert await _derived(pg.maker, document_id) == PURGED
