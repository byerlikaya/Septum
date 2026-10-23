"""Audit events name documents by id only; the read path resolves the name.

Covers the writers (no builder or call site stores ``document_name``), the
audit read endpoints (current name, ``None`` for a deleted document and for
id 0, one batched lookup per page) and the SQLite startup scrub of rows
written before the change.
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest
from audit_scrub_cases import CAP_VALUES, MASKED_QUERY_ROWS, SCRUB_ROWS, duplicated
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from septum_api import database
from septum_api.database import get_db
from septum_api.models import Base
from septum_api.models.audit_event import RETIRED_AUDIT_EXTRA_KEYS, AuditEvent
from septum_api.models.document import Document
from septum_api.models.user import User
from septum_api.routers import audit as audit_router
from septum_api.services import audit_logger
from septum_api.utils.auth_dependency import get_current_user

_API_SOURCE = Path(audit_logger.__file__).resolve().parents[1]
_BUILDERS = (
    "log_pii_detected",
    "log_deanonymization",
    "build_document_event",
    "log_document_event",
    "language_override_event",
)


_RETIRED_KEYS = sorted(RETIRED_AUDIT_EXTRA_KEYS)


@pytest.mark.parametrize("key", _RETIRED_KEYS)
@pytest.mark.parametrize("name", _BUILDERS)
def test_builders_take_no_retired_key(name: str, key: str) -> None:
    assert key not in inspect.signature(getattr(audit_logger, name)).parameters


# The one file allowed to spell the retired keys: where they are defined.
_KEY_ALLOWED = {Path("models/audit_event.py")}


@pytest.mark.parametrize("key", _RETIRED_KEYS)
def test_no_writer_names_a_retired_key(key: str) -> None:
    offenders = []
    for path in _API_SOURCE.rglob("*.py"):
        relative = path.relative_to(_API_SOURCE)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and node.value == key:
                if relative not in _KEY_ALLOWED:
                    offenders.append(f"{relative}:{node.lineno}")
            elif isinstance(node, ast.Call):
                func = node.func
                called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if called in _BUILDERS + ("AuditEvent",) and any(
                    k.arg == key for k in node.keywords
                ):
                    offenders.append(f"{relative}:{node.lineno}")
    assert offenders == []


@pytest.fixture
async def engine(tmp_path: Path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'audit.db'}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
def session_maker(engine):
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def client(session_maker):
    app = FastAPI()
    app.include_router(audit_router.router)

    async def _db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1, email="admin@example.com", hashed_password="x", role="admin", is_active=True
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _document(name: str) -> Document:
    return Document(
        filename=f"{name}.enc",
        original_filename=name,
        encrypted_path=f"/tmp/{name}",
        file_type="application/pdf",
        file_format="pdf",
        detected_language="en",
        file_size_bytes=1,
    )


async def _seed(session_maker) -> tuple[int, int]:
    """Store a live and a deleted document plus events naming each, id 0 and none.

    The second live event still carries stale ``extra.document_name`` and
    ``extra.masked_query`` keys, as a row the scrub has not reached would; it
    is inserted as raw SQL because the model drops those keys on write.
    """
    async with session_maker() as db:
        live, gone = _document("renamed.pdf"), _document("gone.pdf")
        db.add_all([live, gone])
        await db.flush()
        live_id, gone_id = live.id, gone.id
        for document_id in (live_id, gone_id, 0, None):
            db.add(audit_logger.build_document_event(
                document_id=document_id, event_type="document_uploaded", extra={"k": "v"},
            ))
        await db.flush()
        await db.execute(
            text(
                "INSERT INTO audit_events (created_at, event_type, document_id, regulation_ids, "
                "entity_types_detected, entity_count, extra) "
                "VALUES ('2026-01-01', 'pii_detected', :doc, '[]', '{}', 0, :extra)"
            ),
            {
                "doc": live_id,
                "extra": json.dumps({"k": "v", "document_name": "Jane Doe CV.pdf", "masked_query": "Hi [PERSON_1]"}),
            },
        )
        await db.delete(gone)
        await db.commit()
    return live_id, gone_id


def _names(items: list[dict]) -> list[tuple]:
    return sorted(
        ((i["document_id"], i["document_name"]) for i in items),
        key=lambda pair: (pair[0] is None, pair[0] or 0),
    )


async def test_list_returns_current_name_and_null_for_deleted_or_no_document(
    client: AsyncClient, session_maker
) -> None:
    live_id, gone_id = await _seed(session_maker)

    resp = await client.get("/api/audit")

    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert _names(items) == [
        (0, None), (live_id, "renamed.pdf"), (live_id, "renamed.pdf"), (gone_id, None), (None, None),
    ]
    assert all(i["extra"] == {"k": "v"} for i in items)


async def test_list_resolves_names_in_one_query_per_page(
    client: AsyncClient, session_maker, engine
) -> None:
    await _seed(session_maker)
    statements: list[str] = []

    def _record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _record)
    try:
        resp = await client.get("/api/audit", params={"page_size": 200})
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _record)

    assert resp.status_code == 200
    assert len([s for s in statements if "FROM documents" in s]) == 1


async def test_list_skips_the_lookup_when_no_event_names_a_document(
    client: AsyncClient, session_maker, engine
) -> None:
    async with session_maker() as db:
        db.add(audit_logger.build_document_event(document_id=0, event_type="document_uploaded"))
        await db.commit()
    statements: list[str] = []

    def _record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _record)
    try:
        resp = await client.get("/api/audit")
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _record)

    assert resp.json()["items"][0]["document_name"] is None
    assert not [s for s in statements if "FROM documents" in s]


async def test_report_and_session_endpoints_resolve_the_name(
    client: AsyncClient, session_maker
) -> None:
    async with session_maker() as db:
        doc = _document("report.pdf")
        db.add(doc)
        await db.flush()
        doc_id = doc.id
        db.add(AuditEvent(
            event_type="pii_detected", session_id="s-1", document_id=doc_id,
            regulation_ids=[], entity_types_detected={}, entity_count=1, extra={},
        ))
        await db.commit()

    report = await client.get(f"/api/audit/{doc_id}/report")
    session = await client.get("/api/audit/session/s-1")

    assert report.json()["events"][0]["document_name"] == "report.pdf"
    assert session.json()[0]["document_name"] == "report.pdf"


async def test_report_and_session_endpoints_return_null_without_a_live_document(
    client: AsyncClient, session_maker
) -> None:
    async with session_maker() as db:
        gone = _document("gone.pdf")
        db.add(gone)
        await db.flush()
        gone_id = gone.id
        for document_id in (gone_id, 0):
            db.add(AuditEvent(
                event_type="pii_detected", session_id="s-2", document_id=document_id,
                regulation_ids=[], entity_types_detected={}, entity_count=1, extra={},
            ))
        await db.delete(gone)
        await db.commit()

    report = await client.get(f"/api/audit/{gone_id}/report")
    session = await client.get("/api/audit/session/s-2")

    assert [e["document_name"] for e in report.json()["events"]] == [None]
    assert sorted((e["document_id"], e["document_name"]) for e in session.json()) == [
        (0, None), (gone_id, None),
    ]


# The shared cases plus malformed text (only SQLite can hold it) and SQL NULL.
_RAW_SQLITE_ROWS = [
    (stored, sqlite) for stored, _, sqlite in SCRUB_ROWS + MASKED_QUERY_ROWS
] + [("not json", "not json"), (None, None)]


async def test_sqlite_startup_scrub_matches_the_shared_cases(engine) -> None:
    async with engine.begin() as conn:
        for stored, _ in _RAW_SQLITE_ROWS:
            await conn.execute(
                text(
                    "INSERT INTO audit_events (created_at, event_type, regulation_ids, "
                    "entity_types_detected, entity_count, extra) "
                    "VALUES ('2026-01-01', 'pii_detected', '[]', '{}', 0, :extra)"
                ),
                {"extra": stored},
            )

    for _ in range(2):
        await database._sqlite_scrub_retired_audit_keys(engine)
        async with engine.connect() as conn:
            stored = (await conn.execute(text("SELECT extra FROM audit_events ORDER BY id"))).scalars().all()
        assert stored == [expected for _, expected in _RAW_SQLITE_ROWS]


async def _insert_raw_extra(engine, extra: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO audit_events (created_at, event_type, regulation_ids, "
                "entity_types_detected, entity_count, extra) "
                "VALUES ('2026-01-01', 'pii_detected', '[]', '{}', 0, :extra)"
            ),
            {"extra": extra},
        )


@pytest.mark.parametrize("key", sorted(RETIRED_AUDIT_EXTRA_KEYS))
async def test_sqlite_startup_scrub_removes_duplicates_up_to_the_cap(
    engine, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    monkeypatch.setattr(database, "_AUDIT_SCRUB_MAX_PASSES", 2)
    await _insert_raw_extra(engine, duplicated(key, 2))

    await database._sqlite_scrub_retired_audit_keys(engine)

    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT extra FROM audit_events"))).scalar_one() == '{"k":1}'


@pytest.mark.parametrize("key", sorted(RETIRED_AUDIT_EXTRA_KEYS))
async def test_sqlite_startup_scrub_raises_past_the_cap(
    engine, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    monkeypatch.setattr(database, "_AUDIT_SCRUB_MAX_PASSES", 2)
    await _insert_raw_extra(engine, duplicated(key, 3))

    with pytest.raises(RuntimeError) as raised:
        await database._sqlite_scrub_retired_audit_keys(engine)
    assert not any(value in str(raised.value) for value in CAP_VALUES)


async def test_audit_event_never_stores_a_retired_key(session_maker) -> None:
    """Writer-side guard on the model: every ORM write path drops retired keys."""
    async with session_maker() as db:
        db.add(AuditEvent(
            event_type="pii_detected", regulation_ids=[], entity_types_detected={}, entity_count=0,
            extra={"document_name": "Jane Doe CV.pdf", "masked_query": "Hi Jane", "k": 1},
        ))
        db.add(audit_logger.build_document_event(
            document_id=1, event_type="document_uploaded", extra={"masked_query": "q", "k": 2},
        ))
        await db.commit()

    async with session_maker() as db:
        raw = (await db.execute(text("SELECT extra FROM audit_events ORDER BY id"))).scalars().all()
    assert [json.loads(r) for r in raw] == [{"k": 1}, {"k": 2}]


async def test_init_db_scrubs_stale_names_on_sqlite(
    monkeypatch: pytest.MonkeyPatch, engine, session_maker
) -> None:
    await _insert_raw_extra(
        engine, '{"document_name": "Jane Doe CV.pdf", "source": "chat_query", "masked_query": "Hi Jane"}'
    )

    async def _no_seed(*_args, **_kwargs):
        return None

    monkeypatch.setattr(database, "get_engine", lambda: engine)
    monkeypatch.setattr(database, "get_session_maker", lambda: session_maker)
    monkeypatch.setattr(database, "is_sqlite", lambda: True)
    monkeypatch.setattr(database, "_seed_defaults", _no_seed)

    await database.init_db()

    async with session_maker() as db:
        extras = (await db.execute(select(AuditEvent.extra))).scalars().all()
    assert extras == [{"source": "chat_query"}]
