"""Ingestion leases: a run that is alive keeps its document's claim from lapsing.

Each claim writes ``documents.ingestion_lease_expires_at`` in the statement
that sets its run token. While the run is held in this process
(:func:`held`, from before it queues on the ingestion semaphore until it
ends), :func:`heartbeat_loop` pushes the lease forward every
``HEARTBEAT_SECONDS``. A lease that passes without renewal belongs to a run
that is gone.

:func:`fail_expired_ingestions` fails the ``processing`` and ``pending``
rows whose lease lapsed (or that never had one), at startup and then every
``SWEEP_SECONDS`` (:func:`sweep_loop`), so one worker restarting no longer
fails the live runs of another. Every ``PURGE_EVERY_TICKS`` sweep ticks the
loop also retries :func:`purge_failed_documents`, so derived data whose
erasure failed does not wait for a restart.

Every lease is written and compared with :func:`lease_now`, the app's UTC
clock, as a bound parameter: SQLite stores the value as naive text and
compares strings, PostgreSQL stores ``timestamptz``, and both then compare
like with like. Comparisons stay in SQL, never on values read back.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, contextmanager, suppress
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Iterator, List, Optional

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models.document import Document
from .document_pipeline import (
    _holds_claim,
    purge_failed_derived_data,
    purge_failed_documents,
)
from .entity_index_service import drop_index_for_document

logger = logging.getLogger(__name__)

LEASE_SECONDS = 120
HEARTBEAT_SECONDS = 30
SWEEP_SECONDS = 60
PURGE_EVERY_TICKS = 10

INTERRUPTED_ERROR = "Processing interrupted by server restart. Please reprocess."

_held: set[tuple[int, Optional[str]]] = set()


def lease_now() -> datetime:
    """Return the clock every lease is written and compared with: the app's UTC time."""
    return datetime.now(timezone.utc)


def lease_expiry() -> datetime:
    """Return the moment a lease written now lapses."""
    return lease_now() + timedelta(seconds=LEASE_SECONDS)


@contextmanager
def held(document_id: int, run_token: Optional[str]) -> Iterator[None]:
    """Keep the lease of run ``run_token`` on ``document_id`` renewed for the block.

    Keyed by document and token, so a superseded run still in flight and the
    run that replaced it never release each other. Released on any exit,
    cancellation included.
    """
    run = (document_id, run_token)
    _held.add(run)
    try:
        yield
    finally:
        _held.discard(run)


async def renew_all(
    session_maker: Optional[async_sessionmaker[AsyncSession]] = None,
) -> None:
    """Push the lease of every held run forward, each in its own short transaction.

    A renewal matches only while the run still holds its claim on a
    ``processing`` row (the condition :func:`fence_run` uses). A run that
    lost it, to a newer claim or to a reset that failed the row, leaves the
    registry; the run itself finds out at its next fence. With nothing held,
    no session is opened.

    One transaction per run, with the expiry read just before its statement:
    a renewal that waits on a row lock (PostgreSQL: a run's own fenced
    transaction) or on the write lock (SQLite) then holds no lock over the
    other runs' rows meanwhile, commits nothing late on their behalf, and
    does not lose the wait from the lease it writes. One run's failed
    renewal is logged (document id and class name only) and the others still
    renew; that run stays held and is retried on the next pass.
    """
    if not _held:
        return
    if session_maker is None:
        from .. import database

        session_maker = database.get_session_maker()
    for document_id, run_token in list(_held):
        try:
            async with session_maker() as db:
                renewed = await db.scalar(
                    update(Document)
                    .where(_holds_claim(document_id, run_token, require_processing=True))
                    .values(ingestion_lease_expires_at=lease_expiry())
                    .returning(Document.id)
                    .execution_options(synchronize_session=False)
                )
                await db.commit()
        except Exception as exc:
            logger.error(
                "Ingestion lease renewal of document_id=%s failed: %s",
                document_id,
                type(exc).__name__,
            )
            continue
        if renewed is None:
            _held.discard((document_id, run_token))
            logger.warning(
                "Ingestion lease of document_id=%s is no longer held; renewal stopped",
                document_id,
            )


async def heartbeat_loop(
    session_maker: Optional[async_sessionmaker[AsyncSession]] = None,
) -> None:
    """Renew the held leases every ``HEARTBEAT_SECONDS`` until cancelled.

    A failed pass is logged (class name only) and retried on the next tick;
    the lease outlasts a few missed ticks.
    """
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        try:
            await renew_all(session_maker)
        except Exception as exc:
            logger.error("Ingestion lease renewal failed: %s", type(exc).__name__)


async def fail_expired_ingestions(session_maker: async_sessionmaker[AsyncSession]) -> List[int]:
    """Fail every ``processing`` / ``pending`` document whose lease lapsed; return their ids.

    A row without a lease counts as lapsed (written before leases existed,
    or never claimed with one). One conditional UPDATE ... RETURNING sets
    ``failed`` with the restart error and keeps the run token, so the lost
    run can still record its own failure (:func:`fence_run` without
    ``processing``) but no data write; the entity index of each failed
    document is dropped in the same transaction, as a run's own failure
    does. Then :func:`purge_failed_derived_data` erases what the run left
    (never raises; what it cannot erase is retried by :func:`sweep_loop`'s
    periodic purge and the startup purge).

    The clock is read once, before the session opens, and compared in SQL.
    Waiting on a lock only moves that moment into the past, so the sweep
    can be late but never stricter: it fails a row only when no renewal
    that began within ``LEASE_SECONDS`` before the pass started has landed.
    The status and lease are rechecked under the row lock (PostgreSQL
    re-evaluates the condition on the committed row; SQLite holds the
    write lock), so a renewal or claim committed first is honoured.
    """
    now = lease_now()
    lease = Document.ingestion_lease_expires_at
    async with session_maker() as db:
        failed = list(
            await db.scalars(
                update(Document)
                .where(
                    Document.ingestion_status.in_(("processing", "pending")),
                    or_(lease.is_(None), lease < now),
                )
                .values(ingestion_status="failed", ingestion_error=INTERRUPTED_ERROR)
                .returning(Document.id)
                .execution_options(synchronize_session=False)
            )
        )
        for document_id in failed:
            await drop_index_for_document(db, document_id)
        await db.commit()
    for document_id in failed:
        async with session_maker() as db:
            await purge_failed_derived_data(db, document_id, clear_reply_cache=False)
    if failed:
        logger.info("Failed %d ingestions whose lease lapsed", len(failed))
    return failed


async def sweep_loop(
    session_maker: Optional[async_sessionmaker[AsyncSession]] = None,
    purge_every_ticks: int = PURGE_EVERY_TICKS,
) -> None:
    """Run :func:`fail_expired_ingestions` every ``SWEEP_SECONDS`` until cancelled.

    Each tick first renews this process's held runs (:func:`renew_all`), so
    a lease that lapsed while the heartbeat could not run (a host suspend
    stops the sleep, not the wall clock) is renewed, not swept, by its own
    process. Every ``purge_every_ticks``-th tick then runs
    :func:`purge_failed_documents`, which retries the erasures that failed;
    not every tick, since it lists the index directories and the cached map
    keys. The first tick comes ``SWEEP_SECONDS`` after start, so the startup
    purge is not repeated. Without a session maker, the app's is used once
    the database is set up; until then a tick does nothing. A failed pass is
    logged (class name only) and retried on the next tick (the purge on its
    next turn).
    """
    tick = 0
    while True:
        await asyncio.sleep(SWEEP_SECONDS)
        tick += 1
        maker = session_maker
        try:
            if maker is None:
                from .. import database

                if not database.engine_is_ready():
                    continue
                maker = database.get_session_maker()
            await renew_all(maker)
            await fail_expired_ingestions(maker)
        except Exception as exc:
            logger.error("Expired ingestion sweep failed: %s", type(exc).__name__)
        if maker is None or tick % purge_every_ticks:
            continue
        try:
            await purge_failed_documents(maker)
        except Exception as exc:
            logger.error("Periodic purge of failed documents failed: %s", type(exc).__name__)


@asynccontextmanager
async def heartbeat(
    session_maker: Optional[async_sessionmaker[AsyncSession]] = None,
) -> AsyncIterator[None]:
    """Run :func:`heartbeat_loop` for the block, for a process without the API lifespan."""
    task = asyncio.create_task(heartbeat_loop(session_maker), name="septum-ingestion-lease-heartbeat")
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
