"""Ingestion lease renewal: who renews, when it stops, and what holds a run.

A live run keeps its document's lease ahead of the clock; a run that lost
its claim (a newer claim, or a reset that failed the row) is refused and
leaves the registry. Every lease comparison runs in SQL against the same
bound clock value the lease was written with.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select, update

T0 = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


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
async def store(tmp_path):
    """An isolated SQLite store; yields its session maker."""
    from septum_api.models import Base
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'lease.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(bind=engine, expire_on_commit=False)
    await engine.dispose()


async def _add_document(maker, tmp_path, status: str = "completed") -> int:
    from septum_api.models.document import Document

    source = tmp_path / f"source-{status}.enc"
    source.write_bytes(b"ciphertext")
    async with maker() as session:
        doc = Document(
            filename="f", original_filename="f", file_type="x", file_format="pdf",
            detected_language="en", encrypted_path=str(source), file_size_bytes=1,
            ingestion_status=status, user_id=1,
        )
        session.add(doc)
        await session.commit()
        return doc.id


async def _claim(maker, document_id: int) -> str:
    from septum_api.routers import documents

    async with maker() as session:
        token = await documents.claim_for_reingest(session, document_id)
    assert token
    return token


async def _lease_after(maker, document_id: int, moment: datetime) -> bool:
    """Whether the stored lease lies after ``moment``, compared in SQL."""
    from septum_api.models.document import Document

    async with maker() as session:
        return (
            await session.scalar(
                select(Document.id).where(
                    Document.id == document_id,
                    Document.ingestion_lease_expires_at > moment,
                )
            )
        ) is not None


async def _lease_equals(maker, document_id: int, moment: datetime) -> bool:
    from septum_api.models.document import Document

    async with maker() as session:
        return (
            await session.scalar(
                select(Document.id).where(
                    Document.id == document_id,
                    Document.ingestion_lease_expires_at == moment,
                )
            )
        ) is not None


async def _fail_row(maker, document_id: int) -> None:
    from septum_api.models.document import Document

    async with maker() as session:
        await session.execute(
            update(Document).where(Document.id == document_id).values(ingestion_status="failed")
        )
        await session.commit()


def _lease(seconds: int = 0) -> timedelta:
    from septum_api.services import ingestion_lease

    return timedelta(seconds=ingestion_lease.LEASE_SECONDS + seconds)


# ---------------------------------------------------------------- renewal


async def test_renewal_moves_an_expired_lease_of_a_held_run_past_the_clock(
    store, tmp_path, clock
) -> None:
    from septum_api.services import ingestion_lease

    document_id = await _add_document(store, tmp_path)
    token = await _claim(store, document_id)
    clock.now = T0 + _lease(60)
    assert not await _lease_after(store, document_id, clock.now)

    with ingestion_lease.held(document_id, token):
        await ingestion_lease.renew_all(store)

    assert await _lease_equals(store, document_id, clock.now + _lease())


async def test_renewal_is_refused_after_a_reset_failed_the_row_and_the_run_leaves(
    store, tmp_path, clock, caplog
) -> None:
    from septum_api.services import ingestion_lease

    document_id = await _add_document(store, tmp_path)
    token = await _claim(store, document_id)
    await _fail_row(store, document_id)
    clock.now = T0 + _lease(60)

    with caplog.at_level(logging.DEBUG, logger=ingestion_lease.logger.name):
        with ingestion_lease.held(document_id, token):
            await ingestion_lease.renew_all(store)
            assert (document_id, token) not in ingestion_lease._held

    assert await _lease_equals(store, document_id, T0 + _lease())
    messages = [r.getMessage() for r in caplog.records if r.name == ingestion_lease.logger.name]
    assert messages == [f"Ingestion lease of document_id={document_id} is no longer held; renewal stopped"]
    assert token not in caplog.text


async def test_renewal_is_refused_after_a_newer_claim_and_only_the_newer_run_renews(
    store, tmp_path, clock
) -> None:
    from septum_api.services import ingestion_lease

    document_id = await _add_document(store, tmp_path)
    old_token = await _claim(store, document_id)
    await _fail_row(store, document_id)
    clock.now = T0 + timedelta(seconds=10)
    new_token = await _claim(store, document_id)
    clock.now = T0 + timedelta(seconds=40)

    with ingestion_lease.held(document_id, old_token), ingestion_lease.held(document_id, new_token):
        await ingestion_lease.renew_all(store)
        assert ingestion_lease._held == {(document_id, new_token)}

    assert await _lease_equals(store, document_id, clock.now + _lease())


async def test_renewal_with_nothing_held_never_opens_a_session(clock) -> None:
    from septum_api.services import ingestion_lease

    def _no_session():
        raise AssertionError("no session expected")

    await ingestion_lease.renew_all(_no_session)


async def test_a_released_run_is_no_longer_renewed(store, tmp_path, clock) -> None:
    from septum_api.services import ingestion_lease

    document_id = await _add_document(store, tmp_path)
    token = await _claim(store, document_id)
    with ingestion_lease.held(document_id, token):
        assert ingestion_lease._held == {(document_id, token)}
    clock.now = T0 + timedelta(seconds=30)

    await ingestion_lease.renew_all(store)

    assert ingestion_lease._held == set()
    assert await _lease_equals(store, document_id, T0 + _lease())


async def test_one_failing_renewal_is_logged_and_the_other_runs_still_renew(
    store, tmp_path, clock, caplog
) -> None:
    from septum_api.services import ingestion_lease

    first = await _add_document(store, tmp_path)
    second = await _add_document(store, tmp_path)
    runs = {(first, await _claim(store, first)), (second, await _claim(store, second))}
    opened: list[int] = []

    def _first_session_breaks():
        opened.append(1)
        if len(opened) == 1:
            raise RuntimeError("connect /secret/db")
        return store()

    clock.now = T0 + timedelta(seconds=30)
    with caplog.at_level(logging.DEBUG, logger=ingestion_lease.logger.name):
        for run in runs:
            ingestion_lease._held.add(run)
        await ingestion_lease.renew_all(_first_session_breaks)

    renewed = [d for d in (first, second) if await _lease_equals(store, d, clock.now + _lease())]
    kept = [d for d in (first, second) if await _lease_equals(store, d, T0 + _lease())]
    assert len(opened) == 2 and len(renewed) == 1 and len(kept) == 1
    assert ingestion_lease._held == runs
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert [r.getMessage() for r in errors] == [
        f"Ingestion lease renewal of document_id={kept[0]} failed: RuntimeError"
    ]
    assert "/secret/db" not in caplog.text
    for _, token in runs:
        assert token not in caplog.text


# ---------------------------------------------------------------- heartbeat


async def test_the_heartbeat_renews_each_tick_and_survives_a_failing_pass(
    monkeypatch, caplog
) -> None:
    from septum_api.services import ingestion_lease

    monkeypatch.setattr(ingestion_lease, "HEARTBEAT_SECONDS", 0.001)
    passes: list[Any] = []
    done = asyncio.Event()

    async def _renew(session_maker=None) -> None:
        passes.append(session_maker)
        if len(passes) == 1:
            raise RuntimeError("SELECT documents FROM /secret/db")
        if len(passes) == 3:
            done.set()

    monkeypatch.setattr(ingestion_lease, "renew_all", _renew)
    with caplog.at_level(logging.DEBUG, logger=ingestion_lease.logger.name):
        async with ingestion_lease.heartbeat("MAKER"):
            await asyncio.wait_for(done.wait(), timeout=5)

    assert passes[:3] == ["MAKER", "MAKER", "MAKER"]
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert [r.getMessage() for r in errors] == ["Ingestion lease renewal failed: RuntimeError"]
    assert "/secret/db" not in caplog.text


async def test_the_lifespan_starts_the_heartbeat_and_cancels_it_on_shutdown(monkeypatch) -> None:
    from septum_api import main
    from septum_api.services import ingestion_lease

    seen: list[str] = []
    started = asyncio.Event()

    async def _loop(session_maker=None) -> None:
        seen.append("started")
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            seen.append("cancelled")
            raise

    async def _noop(*_args: Any) -> None:
        return None

    monkeypatch.setattr(ingestion_lease, "heartbeat_loop", _loop)
    monkeypatch.setattr(main.bootstrap, "needs_setup", lambda: True)
    monkeypatch.setattr(main, "_announce_setup_token", _noop)
    monkeypatch.setattr(main, "_error_log_retention_loop", _noop)

    async with main.lifespan(main.app):
        await asyncio.wait_for(started.wait(), timeout=5)
        assert seen == ["started"]

    assert seen == ["started", "cancelled"]


# ---------------------------------------------------------------- expiry sweep


async def test_the_sweep_runs_each_tick_and_survives_a_failing_pass(monkeypatch, caplog) -> None:
    from septum_api.services import ingestion_lease

    monkeypatch.setattr(ingestion_lease, "SWEEP_SECONDS", 0.001)
    passes: list[Any] = []
    done = asyncio.Event()

    async def _sweep(session_maker) -> list[int]:
        passes.append(session_maker)
        if len(passes) == 1:
            raise RuntimeError("UPDATE documents FROM /secret/db")
        if len(passes) == 3:
            done.set()
        return []

    monkeypatch.setattr(ingestion_lease, "fail_expired_ingestions", _sweep)
    with caplog.at_level(logging.DEBUG, logger=ingestion_lease.logger.name):
        task = asyncio.create_task(ingestion_lease.sweep_loop("MAKER"))
        try:
            await asyncio.wait_for(done.wait(), timeout=5)
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    assert passes[:3] == ["MAKER", "MAKER", "MAKER"]
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert [r.getMessage() for r in errors] == ["Expired ingestion sweep failed: RuntimeError"]
    assert "/secret/db" not in caplog.text


async def test_the_sweep_skips_its_pass_until_the_database_is_set_up(monkeypatch) -> None:
    from septum_api import database
    from septum_api.services import ingestion_lease

    monkeypatch.setattr(ingestion_lease, "SWEEP_SECONDS", 0.001)
    ready = SimpleNamespace(value=False)
    checks: list[bool] = []
    checked = asyncio.Event()

    def _engine_is_ready() -> bool:
        checks.append(ready.value)
        checked.set()
        return ready.value

    monkeypatch.setattr(database, "engine_is_ready", _engine_is_ready)
    monkeypatch.setattr(database, "get_session_maker", lambda: "LIVE")
    passes: list[Any] = []
    done = asyncio.Event()

    async def _sweep(session_maker) -> list[int]:
        passes.append(session_maker)
        done.set()
        return []

    monkeypatch.setattr(ingestion_lease, "fail_expired_ingestions", _sweep)
    task = asyncio.create_task(ingestion_lease.sweep_loop())
    try:
        await asyncio.wait_for(checked.wait(), timeout=5)
        assert len(checks) >= 1 and checks[0] is False
        assert passes == []
        ready.value = True
        await asyncio.wait_for(done.wait(), timeout=5)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    assert passes[0] == "LIVE"


async def test_each_sweep_tick_renews_the_held_runs_before_it_fails_lapsed_leases(
    store, tmp_path, clock, monkeypatch
) -> None:
    """A run whose lease lapsed while the host slept is renewed, not swept, by its own process."""
    from septum_api.models.document import Document
    from septum_api.services import ingestion_lease

    monkeypatch.setattr(ingestion_lease, "SWEEP_SECONDS", 0.001)
    document_id = await _add_document(store, tmp_path)
    token = await _claim(store, document_id)
    order: list[tuple[str, Any]] = []
    done = asyncio.Event()
    real_renew = ingestion_lease.renew_all
    real_sweep = ingestion_lease.fail_expired_ingestions

    async def _renew(session_maker=None) -> None:
        order.append(("renew", session_maker))
        await real_renew(session_maker)

    async def _sweep(session_maker) -> list[int]:
        order.append(("sweep", session_maker))
        failed = await real_sweep(session_maker)
        done.set()
        return failed

    monkeypatch.setattr(ingestion_lease, "renew_all", _renew)
    monkeypatch.setattr(ingestion_lease, "fail_expired_ingestions", _sweep)
    clock.now = T0 + _lease(60)
    with ingestion_lease.held(document_id, token):
        task = asyncio.create_task(ingestion_lease.sweep_loop(store))
        try:
            await asyncio.wait_for(done.wait(), timeout=5)
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    assert order[:2] == [("renew", store), ("sweep", store)]
    async with store() as session:
        row = await session.get(Document, document_id)
        assert (row.ingestion_status, row.ingestion_run_id) == ("processing", token)
    assert await _lease_equals(store, document_id, clock.now + _lease())


def test_the_lease_clock_is_utc() -> None:
    from septum_api.services import ingestion_lease

    now = ingestion_lease.lease_now()
    assert now.utcoffset() == timedelta(0)
    assert now.tzinfo is timezone.utc


async def test_the_sweep_purges_failed_documents_only_every_nth_tick_and_survives_a_failure(
    monkeypatch, caplog
) -> None:
    from septum_api.services import ingestion_lease

    assert ingestion_lease.PURGE_EVERY_TICKS == 10
    monkeypatch.setattr(ingestion_lease, "SWEEP_SECONDS", 0.001)
    ticks: list[int] = []
    purged_at: list[int] = []
    done = asyncio.Event()

    async def _sweep(session_maker) -> list[int]:
        ticks.append(len(ticks) + 1)
        return []

    async def _purge(session_maker) -> int:
        purged_at.append(len(ticks))
        if len(purged_at) == 1:
            raise RuntimeError("listing /secret/faiss failed")
        done.set()
        return 0

    async def _orphans(session_maker, source_dir) -> None:
        return None

    monkeypatch.setattr(ingestion_lease, "fail_expired_ingestions", _sweep)
    monkeypatch.setattr(ingestion_lease, "purge_failed_documents", _purge)
    monkeypatch.setattr(ingestion_lease, "sweep_orphan_artifacts", _orphans)
    with caplog.at_level(logging.DEBUG, logger=ingestion_lease.logger.name):
        task = asyncio.create_task(ingestion_lease.sweep_loop("MAKER", purge_every_ticks=3))
        try:
            await asyncio.wait_for(done.wait(), timeout=5)
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    assert purged_at == [3, 6]
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors == ["Periodic purge of failed documents failed: RuntimeError"]
    assert "/secret/faiss" not in caplog.text


async def test_the_sweep_runs_the_orphan_sweep_on_each_purge_tick_and_survives_its_failure(
    monkeypatch, caplog
) -> None:
    """Files a hung run wrote for a deleted document do not wait for a restart."""
    from septum_api.services import ingestion_lease
    from septum_api.services.source_files import SOURCE_FILE_DIR

    monkeypatch.setattr(ingestion_lease, "SWEEP_SECONDS", 0)
    ticks: list[int] = []
    order: list[tuple[str, int]] = []
    swept_with: list[tuple[Any, Any]] = []
    done = asyncio.Event()

    async def _sweep(session_maker) -> list[int]:
        ticks.append(len(ticks) + 1)
        return []

    async def _purge(session_maker) -> int:
        order.append(("purge", len(ticks)))
        raise RuntimeError("listing /secret/faiss failed")

    async def _orphans(session_maker, source_dir) -> None:
        order.append(("orphans", len(ticks)))
        swept_with.append((session_maker, source_dir))
        if len(swept_with) == 1:
            raise OSError("listing /secret/documents failed")
        done.set()

    monkeypatch.setattr(ingestion_lease, "fail_expired_ingestions", _sweep)
    monkeypatch.setattr(ingestion_lease, "purge_failed_documents", _purge)
    monkeypatch.setattr(ingestion_lease, "sweep_orphan_artifacts", _orphans)
    with caplog.at_level(logging.DEBUG, logger=ingestion_lease.logger.name):
        task = asyncio.create_task(ingestion_lease.sweep_loop("MAKER", purge_every_ticks=3))
        try:
            await asyncio.wait_for(done.wait(), timeout=5)
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    assert order == [("purge", 3), ("orphans", 3), ("purge", 6), ("orphans", 6)]
    assert swept_with == [("MAKER", SOURCE_FILE_DIR)] * 2
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors == [
        "Periodic purge of failed documents failed: RuntimeError",
        "Periodic orphaned artifact sweep failed: OSError",
        "Periodic purge of failed documents failed: RuntimeError",
    ]
    assert "/secret/" not in caplog.text


async def test_the_lifespan_starts_the_sweep_and_cancels_it_on_shutdown(monkeypatch) -> None:
    from septum_api import main
    from septum_api.services import ingestion_lease

    seen: list[str] = []
    started = asyncio.Event()

    async def _loop(session_maker=None) -> None:
        seen.append("started")
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            seen.append("cancelled")
            raise

    async def _noop(*_args: Any) -> None:
        return None

    monkeypatch.setattr(ingestion_lease, "sweep_loop", _loop)
    monkeypatch.setattr(main.bootstrap, "needs_setup", lambda: True)
    monkeypatch.setattr(main, "_announce_setup_token", _noop)
    monkeypatch.setattr(main, "_error_log_retention_loop", _noop)

    async with main.lifespan(main.app):
        await asyncio.wait_for(started.wait(), timeout=1)
        assert seen == ["started"]

    assert seen == ["started", "cancelled"]


# ---------------------------------------------------------------- runs hold their claim


def _semaphore(monkeypatch) -> asyncio.Semaphore:
    from septum_api.routers import documents

    gate = asyncio.Semaphore(1)
    monkeypatch.setattr(documents, "_INGESTION_SEMAPHORE", gate)
    return gate


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.fixture
def upload_run(store, tmp_path, monkeypatch):
    """Run ``_run_upload_ingestion`` against ``store`` with the ingestion itself replaced."""
    from septum_api import database
    from septum_api.routers import documents
    from septum_api.services import ingestion_lease

    monkeypatch.setattr(database, "get_session_maker", lambda: store)
    state = SimpleNamespace(error=None, held_during=None, failures=[])

    async def _ingest(bg_db, bg_doc, doc_id, source, run_token) -> None:
        state.held_during = set(ingestion_lease._held)
        if state.error is not None:
            raise state.error

    async def _failure(bg_db, doc_id, exc, user_id, *, run_token) -> None:
        state.failures.append((doc_id, type(exc).__name__, run_token))

    monkeypatch.setattr(documents, "_ingest_upload", _ingest)
    monkeypatch.setattr(documents, "record_background_failure", _failure)

    def _run(document_id: int, token: str):
        source = documents._UploadedSource(tmp_path / "s.enc", "application/pdf", "pdf")
        return documents._run_upload_ingestion(document_id, source, 1, run_token=token)

    state.run = _run
    return state


async def test_an_upload_queued_on_the_semaphore_keeps_its_lease_renewed(
    store, tmp_path, clock, monkeypatch, upload_run
) -> None:
    from septum_api.services import ingestion_lease

    gate = _semaphore(monkeypatch)
    document_id = await _add_document(store, tmp_path)
    token = await _claim(store, document_id)

    await gate.acquire()
    task = asyncio.create_task(upload_run.run(document_id, token))
    await _settle()
    assert ingestion_lease._held == {(document_id, token)}
    assert upload_run.held_during is None

    clock.now = T0 + _lease(60)
    await ingestion_lease.renew_all(store)
    assert await _lease_after(store, document_id, clock.now)

    gate.release()
    await asyncio.wait_for(task, timeout=5)
    assert upload_run.held_during == {(document_id, token)}
    assert ingestion_lease._held == set()


async def test_an_upload_that_raises_is_released(store, tmp_path, clock, upload_run) -> None:
    from septum_api.services import ingestion_lease

    document_id = await _add_document(store, tmp_path)
    token = await _claim(store, document_id)
    upload_run.error = ValueError("pipeline broke")

    await asyncio.wait_for(upload_run.run(document_id, token), timeout=5)

    assert upload_run.held_during == {(document_id, token)}
    assert upload_run.failures == [(document_id, "ValueError", token)]
    assert ingestion_lease._held == set()


async def test_an_upload_aborted_by_a_base_exception_is_released(
    store, tmp_path, clock, upload_run
) -> None:
    from septum_api.services import ingestion_lease

    document_id = await _add_document(store, tmp_path)
    token = await _claim(store, document_id)
    upload_run.error = KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        await upload_run.run(document_id, token)

    assert upload_run.held_during == {(document_id, token)}
    assert ingestion_lease._held == set()


async def test_an_upload_cancelled_while_queued_is_released(
    store, tmp_path, clock, monkeypatch, upload_run
) -> None:
    from septum_api.services import ingestion_lease

    gate = _semaphore(monkeypatch)
    document_id = await _add_document(store, tmp_path)
    token = await _claim(store, document_id)

    await gate.acquire()
    task = asyncio.create_task(upload_run.run(document_id, token))
    await _settle()
    assert ingestion_lease._held == {(document_id, token)}
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert ingestion_lease._held == set()
    assert upload_run.held_during is None


@pytest.fixture
def reprocess_run(store, monkeypatch):
    """Call the reprocess endpoint on ``store``; the rebuild itself is replaced."""
    from fastapi import BackgroundTasks
    from septum_api import database
    from septum_api.routers import documents
    from septum_api.services import ingestion_lease

    monkeypatch.setattr(database, "get_session_maker", lambda: store)
    monkeypatch.setattr(documents, "_document_response", lambda doc, current: doc)
    state = SimpleNamespace(error=None, held_during=None, failures=[])

    async def _reingest(bg_db, bg_doc, on_progress=None, *, run_token) -> None:
        state.held_during = set(ingestion_lease._held)
        if state.error is not None:
            raise state.error

    async def _failure(bg_db, doc_id, exc, user_id, *, run_token) -> None:
        state.failures.append((doc_id, type(exc).__name__, run_token))

    monkeypatch.setattr(documents, "reingest_document", _reingest)
    monkeypatch.setattr(documents, "record_background_failure", _failure)

    async def _start(document_id: int) -> tuple[asyncio.Task, str]:
        tasks = BackgroundTasks()
        async with store() as session:
            await documents.reprocess_document(
                document_id, tasks, db=session, user=SimpleNamespace(id=1, role="admin")
            )
        [task] = tasks.tasks
        return asyncio.create_task(task()), task.kwargs["run_token"]

    state.start = _start
    return state


async def test_a_reprocess_queued_on_the_semaphore_holds_its_claim_until_it_raises(
    store, tmp_path, clock, monkeypatch, reprocess_run
) -> None:
    from septum_api.services import ingestion_lease

    gate = _semaphore(monkeypatch)
    document_id = await _add_document(store, tmp_path)
    reprocess_run.error = ValueError("rebuild broke")

    await gate.acquire()
    task, token = await reprocess_run.start(document_id)
    await _settle()
    assert ingestion_lease._held == {(document_id, token)}
    clock.now = T0 + _lease(60)
    await ingestion_lease.renew_all(store)
    assert await _lease_after(store, document_id, clock.now)

    gate.release()
    await asyncio.wait_for(task, timeout=5)

    assert reprocess_run.held_during == {(document_id, token)}
    assert reprocess_run.failures == [(document_id, "ValueError", token)]
    assert ingestion_lease._held == set()


async def test_a_reprocess_cancelled_while_queued_is_released(
    store, tmp_path, clock, monkeypatch, reprocess_run
) -> None:
    from septum_api.services import ingestion_lease

    gate = _semaphore(monkeypatch)
    document_id = await _add_document(store, tmp_path)

    await gate.acquire()
    task, token = await reprocess_run.start(document_id)
    await _settle()
    assert ingestion_lease._held == {(document_id, token)}
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert ingestion_lease._held == set()


# ---------------------------------------------------------------- maintenance script


def _load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "reprocess_entity_detections.py"
    spec = importlib.util.spec_from_file_location("reprocess_script_lease_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_the_script_renews_the_lease_of_the_document_it_rebuilds(
    store, tmp_path, clock, monkeypatch
) -> None:
    from septum_api import bootstrap, database
    from septum_api.routers import documents
    from septum_api.services import ingestion_lease

    monkeypatch.chdir(Path.cwd())
    script = _load_script()
    monkeypatch.setattr(bootstrap, "needs_setup", lambda: False)
    monkeypatch.setattr(
        bootstrap, "get_config", lambda: SimpleNamespace(database_url="sqlite://", db_path="x")
    )
    monkeypatch.setattr(database, "build_database_url", lambda url, path: url)
    monkeypatch.setattr(database, "initialize_engine", lambda url: None)

    async def _no_init() -> None:
        return None

    monkeypatch.setattr(database, "init_db", _no_init)
    monkeypatch.setattr(database, "get_session_maker", lambda: store)
    monkeypatch.setattr(ingestion_lease, "HEARTBEAT_SECONDS", 0.001)
    document_id = await _add_document(store, tmp_path)
    seen: dict[str, Any] = {}

    async def _reingest(db, document, on_progress=None, *, run_token) -> None:
        seen["held"] = set(ingestion_lease._held)
        clock.now = T0 + _lease(60)
        for _ in range(500):
            if await _lease_after(store, document_id, clock.now):
                seen["renewed"] = True
                return
            await asyncio.sleep(0.005)

    monkeypatch.setattr(documents, "reingest_document", _reingest)

    failed = await script._reprocess_all()

    async with store() as session:
        from septum_api.models.document import Document

        token = (await session.get(Document, document_id)).ingestion_run_id
    assert failed == 0
    assert seen == {"held": {(document_id, token)}, "renewed": True}
    assert ingestion_lease._held == set()


# --------------------------------------------------------------- deadline


async def _processing_run(store, tmp_path) -> tuple[int, str]:
    document_id = await _add_document(store, tmp_path)
    return document_id, await _claim(store, document_id)


async def _row_state(maker, document_id: int) -> tuple[str, str | None, str | None]:
    from septum_api.models.document import Document

    async with maker() as session:
        row = (
            await session.execute(
                select(
                    Document.ingestion_status,
                    Document.ingestion_run_id,
                    Document.ingestion_error,
                ).where(Document.id == document_id)
            )
        ).one()
    return tuple(row)


@pytest.fixture
def effects(monkeypatch):
    """Record the index drops and purges the deadline performs."""
    from septum_api.services import ingestion_lease

    calls = SimpleNamespace(dropped=[], purged=[])

    async def _drop(db, document_id):
        calls.dropped.append(document_id)

    async def _purge(db, document_id, *, clear_reply_cache):
        calls.purged.append((document_id, clear_reply_cache))

    monkeypatch.setattr(ingestion_lease, "drop_index_for_document", _drop)
    monkeypatch.setattr(ingestion_lease, "purge_failed_derived_data", _purge)
    return calls


async def test_a_run_past_the_deadline_is_failed_dropped_and_purged(
    store, tmp_path, clock, effects
) -> None:
    from septum_api.services import ingestion_lease

    document_id, token = await _processing_run(store, tmp_path)
    with ingestion_lease.held(document_id, token):
        ingestion_lease.mark_started(document_id, token)
        clock.now = T0 + timedelta(seconds=ingestion_lease.MAX_RUN_SECONDS)
        await ingestion_lease.renew_all(store)
        assert (document_id, token) not in ingestion_lease._held

    assert await _row_state(store, document_id) == (
        "failed", token, ingestion_lease.TIMEOUT_ERROR
    )
    assert effects.dropped == [document_id]
    assert effects.purged == [(document_id, False)]


async def test_a_run_inside_the_deadline_is_renewed_as_before(
    store, tmp_path, clock, effects
) -> None:
    from septum_api.services import ingestion_lease

    document_id, token = await _processing_run(store, tmp_path)
    with ingestion_lease.held(document_id, token):
        ingestion_lease.mark_started(document_id, token)
        clock.now = T0 + timedelta(seconds=ingestion_lease.MAX_RUN_SECONDS - 1)
        await ingestion_lease.renew_all(store)
        assert (document_id, token) in ingestion_lease._held

    assert (await _row_state(store, document_id))[0] == "processing"
    assert await _lease_equals(store, document_id, clock.now + _lease())
    assert effects.dropped == [] and effects.purged == []


async def test_an_overdue_run_whose_row_was_superseded_writes_nothing(
    store, tmp_path, clock, effects
) -> None:
    from septum_api.services import ingestion_lease

    document_id, old_token = await _processing_run(store, tmp_path)
    await _fail_row(store, document_id)
    new_token = await _claim(store, document_id)
    with ingestion_lease.held(document_id, old_token):
        ingestion_lease.mark_started(document_id, old_token)
        clock.now = T0 + timedelta(seconds=ingestion_lease.MAX_RUN_SECONDS + 1)
        await ingestion_lease.renew_all(store)
        assert (document_id, old_token) not in ingestion_lease._held

    assert await _row_state(store, document_id) == ("processing", new_token, None)
    assert effects.dropped == [] and effects.purged == []


async def test_time_queued_before_mark_started_does_not_count(
    store, tmp_path, clock, effects
) -> None:
    from septum_api.services import ingestion_lease

    document_id, token = await _processing_run(store, tmp_path)
    with ingestion_lease.held(document_id, token):
        clock.now = T0 + timedelta(seconds=ingestion_lease.MAX_RUN_SECONDS * 3)
        await ingestion_lease.renew_all(store)
        assert (document_id, token) in ingestion_lease._held
        assert (await _row_state(store, document_id))[0] == "processing"

        ingestion_lease.mark_started(document_id, token)
        clock.now += timedelta(seconds=ingestion_lease.MAX_RUN_SECONDS - 1)
        await ingestion_lease.renew_all(store)
        assert (document_id, token) in ingestion_lease._held

        clock.now += timedelta(seconds=1)
        await ingestion_lease.renew_all(store)
        assert (document_id, token) not in ingestion_lease._held

    assert (await _row_state(store, document_id))[2] == ingestion_lease.TIMEOUT_ERROR


async def test_mark_started_ignores_a_run_that_is_not_held(clock) -> None:
    from septum_api.services import ingestion_lease

    ingestion_lease.mark_started(1, "t")
    assert ingestion_lease._started == {}


async def test_an_overdue_run_whose_failure_raises_is_logged_and_the_other_runs_still_renew(
    store, tmp_path, clock, effects, monkeypatch, caplog
) -> None:
    from septum_api.services import ingestion_lease

    overdue_id, overdue_token = await _processing_run(store, tmp_path)
    live_id = await _add_document(store, tmp_path, "pending")
    live_token = await _claim(store, live_id)

    async def _boom(*_args: Any) -> None:
        raise RuntimeError("database gone")

    monkeypatch.setattr(ingestion_lease, "_fail_overdue_run", _boom)
    with ingestion_lease.held(overdue_id, overdue_token), ingestion_lease.held(
        live_id, live_token
    ):
        ingestion_lease.mark_started(overdue_id, overdue_token)
        clock.now = T0 + timedelta(seconds=ingestion_lease.MAX_RUN_SECONDS)
        with caplog.at_level(logging.ERROR, logger=ingestion_lease.logger.name):
            await ingestion_lease.renew_all(store)
        assert ingestion_lease._held == {(live_id, live_token)}
        assert ingestion_lease._started == {}

    assert "RuntimeError" in caplog.text
    assert "database gone" not in caplog.text
    assert await _lease_equals(store, live_id, clock.now + _lease())


async def test_the_deadline_clock_is_dropped_when_a_run_is_released_or_refused(
    store, tmp_path, clock
) -> None:
    from septum_api.services import ingestion_lease

    released_id, released_token = await _processing_run(store, tmp_path)
    with ingestion_lease.held(released_id, released_token):
        ingestion_lease.mark_started(released_id, released_token)
        assert set(ingestion_lease._started) == {(released_id, released_token)}
    assert ingestion_lease._started == {}

    refused_id, refused_token = await _processing_run(store, tmp_path)
    with ingestion_lease.held(refused_id, refused_token):
        ingestion_lease.mark_started(refused_id, refused_token)
        await _fail_row(store, refused_id)
        await ingestion_lease.renew_all(store)
        assert ingestion_lease._held == set()
        assert ingestion_lease._started == {}


async def test_an_upload_starts_its_deadline_only_after_it_holds_the_semaphore(
    store, tmp_path, clock, monkeypatch, upload_run
) -> None:
    from septum_api.routers import documents

    gate = _semaphore(monkeypatch)
    started: list[tuple[int, str]] = []
    monkeypatch.setattr(documents, "mark_started", lambda doc, tok: started.append((doc, tok)))
    document_id = await _add_document(store, tmp_path)
    token = await _claim(store, document_id)

    await gate.acquire()
    task = asyncio.create_task(upload_run.run(document_id, token))
    await _settle()
    assert started == []

    gate.release()
    await asyncio.wait_for(task, timeout=5)
    assert started == [(document_id, token)]


async def test_a_reprocess_starts_its_deadline_only_after_it_holds_the_semaphore(
    store, tmp_path, clock, monkeypatch, reprocess_run
) -> None:
    from septum_api.routers import documents

    gate = _semaphore(monkeypatch)
    started: list[tuple[int, str]] = []
    monkeypatch.setattr(documents, "mark_started", lambda doc, tok: started.append((doc, tok)))
    document_id = await _add_document(store, tmp_path)

    await gate.acquire()
    task, token = await reprocess_run.start(document_id)
    await _settle()
    assert started == []

    gate.release()
    await asyncio.wait_for(task, timeout=5)
    assert started == [(document_id, token)]
