"""Bounds on the spreadsheet schema PUT body, the schema a PUT may touch, and the chat turn.

Also the race-safe first create of a document's schema (PUT and ingestion).

Body bounds are 422s raised before the handler runs. The bounds that need the
stored schema (existing columns only, labelled-column budget) are 422s raised
in the handler before the PII gate. The gate is replaced by a spy: these tests
assert on what reaches it, not on what it returns. Labels stored before the
bounds existed must still render through GET and still be capped in chat.

This module keeps its own ``env`` fixture: the shared one in
``document_env_fixtures`` builds different documents and has no gate spy.
"""

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import pytest
from httpx import ASGITransport, AsyncClient
from septum_api.database import build_default_app_settings, get_db
from septum_api.main import app
from septum_api.models import Base
from septum_api.models.document import Document
from septum_api.models.spreadsheet_schema import SpreadsheetColumn, SpreadsheetSchema
from septum_api.models.user import User
from septum_api.request_limits import (
    MAX_SCHEMA_LABEL_CHARS,
    MAX_SCHEMA_LABELED_COLUMNS,
    MAX_SHEET_COLUMNS,
    MAX_TECHNICAL_LABEL_CHARS,
)
from septum_api.routers import chat as chat_module
from septum_api.routers import documents as documents_router
from septum_api.services.policy_snapshot import current_policy_snapshot
from septum_api.utils.auth_dependency import get_current_user
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

SchemaRow = tuple[int, str, Optional[str]]

POSTGRES_URL = os.getenv("SEPTUM_TEST_POSTGRES_URL", "")


class _Env:
    def __init__(self, session_maker: Any, client: AsyncClient, user: User) -> None:
        self.session_maker = session_maker
        self.client = client
        self.user = user
        self.gate_calls: list[list[str]] = []

    async def add_document(self) -> int:
        async with self.session_maker() as session:
            doc = Document(
                filename="stored.xlsx",
                original_filename="sheet.xlsx",
                file_type=(
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                ),
                file_format="xlsx",
                detected_language="en",
                encrypted_path="/nonexistent",
                file_size_bytes=10,
                ocr_confidence=0.0,
                active_regulation_ids=[],
                sanitized_entity_types=[],
                sanitized_policy_digest=(await current_policy_snapshot(session)).digest,
                sanitized_language="en",
                ingestion_status="completed",
                chunk_count=0,
                user_id=self.user.id,
            )
            session.add(doc)
            await session.commit()
            return doc.id

    async def add_schema(self, doc: int, columns: list[SchemaRow]) -> None:
        async with self.session_maker() as session:
            schema = SpreadsheetSchema(document_id=doc)
            session.add(schema)
            await session.flush()
            session.add_all(
                SpreadsheetColumn(
                    schema_id=schema.id,
                    index=index,
                    technical_label=technical,
                    semantic_label=semantic,
                    is_numeric=False,
                )
                for index, technical, semantic in columns
            )
            await session.commit()

    async def _scalar(self, statement: Any) -> Any:
        async with self.session_maker() as session:
            return await session.scalar(statement)

    async def stored_column_count(self) -> int:
        return int(await self._scalar(select(func.count(SpreadsheetColumn.id))))

    async def stored_labelled_count(self) -> int:
        return int(
            await self._scalar(
                select(func.count(SpreadsheetColumn.id)).where(
                    SpreadsheetColumn.semantic_label.is_not(None)
                )
            )
        )

    async def stored_semantic(self, index: int) -> Optional[str]:
        return await self._scalar(
            select(SpreadsheetColumn.semantic_label).where(
                SpreadsheetColumn.index == index
            )
        )


@asynccontextmanager
async def _sqlite_engine(tmp_path: Path) -> AsyncIterator[Any]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'schema_limits.db'}")
    try:
        yield engine
    finally:
        await engine.dispose()


@asynccontextmanager
async def _postgres_engine() -> AsyncIterator[Any]:
    schema = f"schema_limits_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(POSTGRES_URL)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        POSTGRES_URL, connect_args={"server_settings": {"search_path": schema}}
    )
    try:
        yield engine
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@pytest.fixture
def backend(request: pytest.FixtureRequest) -> str:
    return getattr(request, "param", "sqlite")


@pytest.fixture
async def env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> AsyncIterator[_Env]:
    if backend == "postgresql" and not POSTGRES_URL:
        pytest.skip("SEPTUM_TEST_POSTGRES_URL not set")
    async with (
        _sqlite_engine(tmp_path) if backend == "sqlite" else _postgres_engine()
    ) as engine:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async for ready in _serve(engine, monkeypatch):
            yield ready


async def _serve(engine: Any, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Env]:
    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with session_maker() as session:
        user = User(email="editor@example.com", hashed_password="x", role="editor")
        session.add(user)
        await session.commit()
        session.add(build_default_app_settings())
        await session.commit()

    async def _override_get_db():
        async with session_maker() as session:
            yield session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        env = _Env(session_maker, client, user)

        async def _spy_document_gate(db, settings, document, texts, sanitizer=None):
            env.gate_calls.append(list(texts))
            return list(texts)

        async def _spy_chat_gate(texts, anon_map, db, settings, **kwargs):
            env.gate_calls.append(list(texts))
            return list(texts)

        monkeypatch.setattr(documents_router, "gate_document_texts", _spy_document_gate)
        monkeypatch.setattr(chat_module, "gate_untrusted_texts", _spy_chat_gate)
        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user] = lambda: env.user
        try:
            yield env
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)


def _plain_columns(count: int) -> list[SchemaRow]:
    return [(i, f"COLUMN_{i + 1}", None) for i in range(count)]


def _labelled_columns(count: int) -> list[SchemaRow]:
    return [(i, f"COLUMN_{i + 1}", f"L{i}") for i in range(count)]


def _column(index: int = 0, **overrides: Any) -> dict[str, Any]:
    column: dict[str, Any] = {
        "index": index,
        "technical_label": "COLUMN_1",
        "semantic_label": "Salary",
        "is_numeric": True,
    }
    column.update(overrides)
    return column


def _payload(indexes: Any, label_of: Any = lambda i: f"L{i}") -> list[dict[str, Any]]:
    return [
        _column(index=i, technical_label=f"COLUMN_{i + 1}", semantic_label=label_of(i))
        for i in indexes
    ]


async def _put(env: _Env, doc: int, columns: list[dict[str, Any]]):
    return await env.client.put(
        f"/api/documents/{doc}/schema", json={"columns": columns}
    )


def _assert_rejected_before_gate(env: _Env, resp: Any) -> None:
    assert resp.status_code == 422, resp.text
    assert env.gate_calls == []


async def _describe(env: _Env, doc: int) -> list[str]:
    async with env.session_maker() as session:
        return await chat_module._schema_column_descriptions(
            session, doc, object(), object()
        )


class TestPayloadRejectedBeforeGate:
    async def test_overlong_semantic_label_is_422_and_not_gated(self, env: _Env) -> None:
        doc = await env.add_document()
        resp = await _put(
            env, doc, [_column(semantic_label="x" * (MAX_SCHEMA_LABEL_CHARS + 1))]
        )

        _assert_rejected_before_gate(env, resp)
        assert await env.stored_column_count() == 0

    async def test_overlong_technical_label_is_422_and_not_gated(self, env: _Env) -> None:
        doc = await env.add_document()
        resp = await _put(
            env, doc, [_column(technical_label="C" * (MAX_TECHNICAL_LABEL_CHARS + 1))]
        )

        _assert_rejected_before_gate(env, resp)

    async def test_more_entries_than_sheet_slots_is_422_by_uniqueness(
        self, env: _Env
    ) -> None:
        # Every index 0..16383 is in range and unique, plus one duplicate:
        # only the uniqueness rule fires, so this isolates it. It also pins
        # that the list itself has no stricter cap than the sheet.
        doc = await env.add_document()
        columns = [_column(index=i, semantic_label=None) for i in range(MAX_SHEET_COLUMNS)]
        columns.append(_column(index=0, semantic_label=None))
        resp = await _put(env, doc, columns)

        _assert_rejected_before_gate(env, resp)
        assert await env.stored_column_count() == 0

    async def test_too_many_labels_in_one_payload_is_422_and_not_gated(
        self, env: _Env
    ) -> None:
        doc = await env.add_document()
        resp = await _put(
            env, doc, _payload(range(MAX_SCHEMA_LABELED_COLUMNS + 1), lambda i: f"Label {i}")
        )

        _assert_rejected_before_gate(env, resp)
        assert await env.stored_column_count() == 0

    @pytest.mark.parametrize("index", [-1, MAX_SHEET_COLUMNS, 2**31])
    async def test_out_of_range_index_is_422_and_not_gated(
        self, env: _Env, index: int
    ) -> None:
        doc = await env.add_document()
        resp = await _put(env, doc, [_column(index=index)])

        _assert_rejected_before_gate(env, resp)

    async def test_duplicate_index_is_422_and_not_gated(self, env: _Env) -> None:
        doc = await env.add_document()
        resp = await _put(
            env, doc, [_column(index=2), _column(index=2, semantic_label="Other")]
        )

        _assert_rejected_before_gate(env, resp)
        assert await env.stored_column_count() == 0


class TestUpdateFitsStoredSchema:
    async def test_index_missing_from_sparse_schema_is_422_and_not_gated(
        self, env: _Env
    ) -> None:
        doc = await env.add_document()
        await env.add_schema(doc, [(0, "COLUMN_1", None), (5, "COLUMN_6", None)])

        resp = await _put(
            env, doc, [_column(index=1, technical_label="COLUMN_2", semantic_label="Tax")]
        )

        _assert_rejected_before_gate(env, resp)
        assert await env.stored_column_count() == 2

    async def test_existing_index_of_sparse_schema_is_accepted(self, env: _Env) -> None:
        doc = await env.add_document()
        await env.add_schema(doc, [(0, "COLUMN_1", None), (5, "COLUMN_6", None)])

        resp = await _put(
            env, doc, [_column(index=5, technical_label="COLUMN_6", semantic_label="Tax")]
        )

        assert resp.status_code == 200, resp.text
        assert env.gate_calls == [["Tax"]]

    async def test_any_index_is_422_for_a_schema_without_columns(self, env: _Env) -> None:
        doc = await env.add_document()
        await env.add_schema(doc, [])

        resp = await _put(env, doc, [_column(index=0, semantic_label="Tax")])

        _assert_rejected_before_gate(env, resp)

    async def test_any_index_is_accepted_when_no_schema_exists(self, env: _Env) -> None:
        doc = await env.add_document()

        resp = await _put(
            env, doc, [_column(index=MAX_SHEET_COLUMNS - 1, semantic_label=None)]
        )

        assert resp.status_code == 200, resp.text


class TestLabelledColumnBudget:
    async def test_labels_accumulating_across_puts_are_422_at_the_put_that_exceeds(
        self, env: _Env
    ) -> None:
        doc = await env.add_document()
        await env.add_schema(doc, _plain_columns(2000))

        first = await _put(env, doc, _payload(range(600)))
        assert first.status_code == 200, first.text
        assert len(env.gate_calls) == 1

        second = await _put(env, doc, _payload(range(600, 1200)))
        assert second.status_code == 422, second.text
        assert len(env.gate_calls) == 1
        assert await env.stored_labelled_count() == 600

        third = await _put(env, doc, _payload(range(600, 1024)))
        assert third.status_code == 200, third.text
        assert await env.stored_labelled_count() == MAX_SCHEMA_LABELED_COLUMNS

        fourth = await _put(env, doc, _payload([1024], lambda i: "Over"))
        assert fourth.status_code == 422
        assert len(env.gate_calls) == 2

    async def test_relabelling_stored_labels_does_not_count_twice(self, env: _Env) -> None:
        doc = await env.add_document()
        await env.add_schema(doc, _labelled_columns(MAX_SCHEMA_LABELED_COLUMNS))

        resp = await _put(
            env, doc, _payload(range(MAX_SCHEMA_LABELED_COLUMNS), lambda i: f"M{i}")
        )

        assert resp.status_code == 200, resp.text
        assert await env.stored_labelled_count() == MAX_SCHEMA_LABELED_COLUMNS


class TestBoundaryAccepted:
    async def test_limit_values_pass_and_reach_the_gate(self, env: _Env) -> None:
        doc = await env.add_document()
        await env.add_schema(
            doc, [(MAX_SHEET_COLUMNS - 1, "C" * MAX_TECHNICAL_LABEL_CHARS, None)]
        )

        resp = await _put(
            env,
            doc,
            [
                _column(
                    index=MAX_SHEET_COLUMNS - 1,
                    technical_label="C" * MAX_TECHNICAL_LABEL_CHARS,
                    semantic_label="s" * MAX_SCHEMA_LABEL_CHARS,
                )
            ],
        )

        assert resp.status_code == 200, resp.text
        assert env.gate_calls == [["s" * MAX_SCHEMA_LABEL_CHARS]]

    async def test_maximum_columns_with_maximum_labels_pass(self, env: _Env) -> None:
        doc = await env.add_document()
        columns = [
            {
                "index": i,
                "technical_label": f"COLUMN_{i + 1}",
                "semantic_label": f"Label {i}" if i < MAX_SCHEMA_LABELED_COLUMNS else None,
                "is_numeric": False,
            }
            for i in range(MAX_SHEET_COLUMNS)
        ]
        resp = await _put(env, doc, columns)

        assert resp.status_code == 200, resp.text
        assert len(env.gate_calls) == 1
        assert len(env.gate_calls[0]) == MAX_SCHEMA_LABELED_COLUMNS
        assert await env.stored_column_count() == MAX_SHEET_COLUMNS


class TestConcurrentUpdates:
    @pytest.mark.parametrize("backend", ["sqlite", "postgresql"], indirect=True)
    async def test_put_that_passed_the_gate_late_is_rechecked_before_writing(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        doc = await env.add_document()
        await env.add_schema(doc, _plain_columns(2000))
        first_in_gate = asyncio.Event()
        release_first = asyncio.Event()
        gate_entries = 0

        async def _gate(db, settings, document, texts, sanitizer=None):
            nonlocal gate_entries
            gate_entries += 1
            if gate_entries == 1:
                first_in_gate.set()
                await release_first.wait()
            env.gate_calls.append(list(texts))
            return list(texts)

        monkeypatch.setattr(documents_router, "gate_document_texts", _gate)
        first = asyncio.create_task(_put(env, doc, _payload(range(600))))
        await first_in_gate.wait()
        second = await _put(env, doc, _payload(range(600, 1200)))
        release_first.set()
        first_response = await first

        assert second.status_code == 200, second.text
        assert first_response.status_code == 422, first_response.text
        assert await env.stored_labelled_count() == 600


_BOTH_BACKENDS = pytest.mark.parametrize("backend", ["sqlite", "postgresql"], indirect=True)


async def _stored_schemas(env: _Env, doc: int) -> list[list[tuple[int, Optional[str]]]]:
    """Return each stored schema of ``doc`` as its (index, semantic_label) rows."""
    async with env.session_maker() as session:
        schemas = (
            await session.scalars(
                select(SpreadsheetSchema)
                .options(selectinload(SpreadsheetSchema.columns))
                .where(SpreadsheetSchema.document_id == doc)
            )
        ).all()
        return [[(c.index, c.semantic_label) for c in schema.columns] for schema in schemas]


def _hold_first_creates(
    monkeypatch: pytest.MonkeyPatch, backend: str
) -> list[bool]:
    """Make two concurrent PUTs both see no schema before either creates one.

    Only on PostgreSQL: on SQLite the first PUT's lock write holds the
    database write lock, so the second waits there and sees the first's
    schema. Returns the ``required`` flag of every lock-and-read call.
    """
    real = documents_router._lock_and_read_schema
    calls: list[bool] = []
    both_empty = asyncio.Event()
    empty_reads = 0

    async def _held(db, document_id, *, required=False):
        nonlocal empty_reads
        calls.append(required)
        schema = await real(db, document_id, required=required)
        if schema is None and backend == "postgresql":
            empty_reads += 1
            if empty_reads == 2:
                both_empty.set()
            # Failure guard only: a regression must fail, not hang the run.
            await asyncio.wait_for(both_empty.wait(), 10)
        return schema

    monkeypatch.setattr(documents_router, "_lock_and_read_schema", _held)
    return calls


class TestConcurrentFirstCreate:
    """Unique indexes make the first create of a document's schema race-safe."""

    @_BOTH_BACKENDS
    async def test_concurrent_first_puts_with_the_same_columns_both_succeed(
        self, env: _Env, backend: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        doc = await env.add_document()
        calls = _hold_first_creates(monkeypatch, backend)

        first, second = await asyncio.gather(
            _put(env, doc, _payload(range(3), lambda i: f"A{i}")),
            _put(env, doc, _payload(range(3), lambda i: f"B{i}")),
        )

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        [columns] = await _stored_schemas(env, doc)
        assert [index for index, _ in columns] == [0, 1, 2]
        assert {label[0] for _, label in columns} in ({"A"}, {"B"})
        # On PostgreSQL the loser's insert hit the unique index and re-read the winner.
        assert calls.count(True) == (1 if backend == "postgresql" else 0)

    @_BOTH_BACKENDS
    async def test_concurrent_first_puts_with_other_columns_resolve_as_if_sequential(
        self, env: _Env, backend: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        doc = await env.add_document()
        _hold_first_creates(monkeypatch, backend)
        payloads = [_payload([0, 1]), _payload([2])]

        responses = await asyncio.gather(*(_put(env, doc, p) for p in payloads))

        statuses = [r.status_code for r in responses]
        assert sorted(statuses) == [200, 422], [r.text for r in responses]
        winner = payloads[statuses.index(200)]
        [columns] = await _stored_schemas(env, doc)
        assert columns == [(c["index"], c["semantic_label"]) for c in winner]

    @_BOTH_BACKENDS
    async def test_ingestion_create_losing_to_a_put_keeps_the_put_schema(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        doc = await env.add_document()
        real = documents_router._insert_schema_unless_raced
        results: list[bool] = []

        async def _put_wins_first(db, schema):
            await env.add_schema(doc, [(0, "COLUMN_1", "Salary")])
            results.append(await real(db, schema))
            return results[-1]

        monkeypatch.setattr(documents_router, "_insert_schema_unless_raced", _put_wins_first)
        async with env.session_maker() as session:
            await documents_router._ensure_spreadsheet_schema(
                session, doc, {"sheets": [{"column_count": 3}]}
            )
            assert not session.in_transaction()
            assert await session.scalar(select(func.count(Document.id))) == 1

        assert results == [False]
        assert await _stored_schemas(env, doc) == [[(0, "Salary")]]

    @_BOTH_BACKENDS
    @pytest.mark.parametrize(
        ("stored", "update", "status"),
        [
            pytest.param(_plain_columns(2), _payload([1]), 200, id="fits"),
            pytest.param(_plain_columns(1), _payload([1]), 422, id="new-column"),
            pytest.param(
                _labelled_columns(MAX_SCHEMA_LABELED_COLUMNS)
                + [(MAX_SCHEMA_LABELED_COLUMNS, f"COLUMN_{MAX_SCHEMA_LABELED_COLUMNS + 1}", None)],
                _payload([MAX_SCHEMA_LABELED_COLUMNS]),
                422,
                id="over-label-budget",
            ),
        ],
    )
    async def test_put_losing_the_create_is_rechecked_against_the_winner(
        self,
        env: _Env,
        monkeypatch: pytest.MonkeyPatch,
        stored: list[SchemaRow],
        update: list[dict[str, Any]],
        status: int,
    ) -> None:
        # The winner commits while the PUT is in the gate; the PUT's locked
        # read is made stale, so its insert hits the unique index.
        doc = await env.add_document()
        real_lock = documents_router._lock_and_read_schema
        lock_calls: list[bool] = []

        async def _gate(db, settings, document, texts, sanitizer=None):
            await env.add_schema(doc, stored)
            return list(texts)

        async def _stale_first(db, document_id, *, required=False):
            lock_calls.append(required)
            schema = await real_lock(db, document_id, required=required)
            return None if len(lock_calls) == 1 else schema

        monkeypatch.setattr(documents_router, "gate_document_texts", _gate)
        monkeypatch.setattr(documents_router, "_lock_and_read_schema", _stale_first)
        resp = await _put(env, doc, update)

        assert resp.status_code == status, resp.text
        assert lock_calls == [False, True]
        expected = [(index, semantic) for index, _, semantic in stored]
        if status == 200:
            expected = [(index, "L1" if index == 1 else semantic) for index, semantic in expected]
        assert await _stored_schemas(env, doc) == [expected]

    @pytest.mark.parametrize("backend", ["postgresql"], indirect=True)
    async def test_put_create_losing_to_ingestion_applies_to_the_ingested_schema(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # PostgreSQL only: on SQLite the PUT holds the write lock by then, so
        # ingestion could not commit in between.
        doc = await env.add_document()
        real = documents_router._insert_schema_unless_raced
        ingested = False

        async def _ingestion_wins_first(db, schema):
            nonlocal ingested
            if not ingested:
                ingested = True
                async with env.session_maker() as session:
                    await documents_router._ensure_spreadsheet_schema(
                        session, doc, {"sheets": [{"column_count": 3}]}
                    )
            return await real(db, schema)

        monkeypatch.setattr(
            documents_router, "_insert_schema_unless_raced", _ingestion_wins_first
        )
        resp = await _put(env, doc, _payload([1]))

        assert resp.status_code == 200, resp.text
        assert await _stored_schemas(env, doc) == [[(0, None), (1, "L1"), (2, None)]]

    @_BOTH_BACKENDS
    async def test_other_integrity_errors_are_raised(self, env: _Env) -> None:
        doc = await env.add_document()
        schema = SpreadsheetSchema(document_id=doc)
        schema.columns = [
            SpreadsheetColumn(index=0, technical_label="COLUMN_1"),
            SpreadsheetColumn(index=0, technical_label="COLUMN_1"),
        ]

        async with env.session_maker() as session:
            with pytest.raises(IntegrityError):
                await documents_router._insert_schema_unless_raced(session, schema)

        assert await _stored_schemas(env, doc) == []

    @_BOTH_BACKENDS
    async def test_second_schema_for_a_document_is_refused(self, env: _Env) -> None:
        doc = await env.add_document()
        await env.add_schema(doc, _plain_columns(1))

        with pytest.raises(IntegrityError):
            await env.add_schema(doc, _plain_columns(1))

        assert await _stored_schemas(env, doc) == [[(0, None)]]


class TestTechnicalLabelMismatch:
    async def test_mismatched_label_cannot_fill_an_unlabelled_column_at_the_budget(
        self, env: _Env
    ) -> None:
        doc = await env.add_document()
        await env.add_schema(
            doc,
            _labelled_columns(MAX_SCHEMA_LABELED_COLUMNS)
            + [(i, f"COLUMN_{i + 1}", None) for i in range(MAX_SCHEMA_LABELED_COLUMNS, 1050)],
        )

        resp = await _put(
            env,
            doc,
            [_column(index=1040, technical_label="WRONG", semantic_label="New")],
        )

        assert resp.status_code == 200, resp.text
        assert await env.stored_semantic(1040) is None
        assert await env.stored_labelled_count() == MAX_SCHEMA_LABELED_COLUMNS

    async def test_mismatched_put_keeps_the_stored_label_of_a_labelled_column(
        self, env: _Env
    ) -> None:
        doc = await env.add_document()
        await env.add_schema(doc, [(0, "COLUMN_1", "Kept"), (1, "COLUMN_2", None)])

        resp = await _put(
            env, doc, [_column(index=0, technical_label="WRONG", semantic_label="Changed")]
        )

        assert resp.status_code == 200, resp.text
        assert await env.stored_semantic(0) == "Kept"


class TestClearingFreesBudget:
    async def test_clearing_and_labelling_in_one_put_stays_within_the_budget(
        self, env: _Env
    ) -> None:
        doc = await env.add_document()
        await env.add_schema(
            doc,
            _labelled_columns(MAX_SCHEMA_LABELED_COLUMNS)
            + [(i, f"COLUMN_{i + 1}", None) for i in range(MAX_SCHEMA_LABELED_COLUMNS, 1536)],
        )

        resp = await _put(
            env,
            doc,
            _payload(range(0, 512), lambda i: None)
            + _payload(range(MAX_SCHEMA_LABELED_COLUMNS, 1536)),
        )

        assert resp.status_code == 200, resp.text
        assert await env.stored_labelled_count() == MAX_SCHEMA_LABELED_COLUMNS
        assert await env.stored_semantic(0) is None
        assert len(env.gate_calls) == 1
        assert len(env.gate_calls[0]) == 512


class TestChatBudget:
    async def test_chat_gates_and_describes_at_most_the_labelled_budget(
        self, env: _Env
    ) -> None:
        doc = await env.add_document()
        await env.add_schema(doc, _labelled_columns(MAX_SCHEMA_LABELED_COLUMNS + 76))

        descriptions = await _describe(env, doc)

        assert len(env.gate_calls) == 1
        assert env.gate_calls[0] == [f"L{i}" for i in range(MAX_SCHEMA_LABELED_COLUMNS)]
        assert len(descriptions) == MAX_SCHEMA_LABELED_COLUMNS + 1
        assert descriptions[MAX_SCHEMA_LABELED_COLUMNS - 1] == (
            f"index {MAX_SCHEMA_LABELED_COLUMNS - 1} → L{MAX_SCHEMA_LABELED_COLUMNS - 1}"
        )
        assert descriptions[-1] == "76 further columns are not described"

    async def test_interleaved_blank_labels_are_skipped_and_not_counted(
        self, env: _Env
    ) -> None:
        doc = await env.add_document()
        await env.add_schema(
            doc,
            [
                (
                    i,
                    f"COLUMN_{i + 1}",
                    f"L{i}" if i % 2 == 0 else ("" if i % 4 == 1 else "   "),
                )
                for i in range(2050)
            ],
        )

        descriptions = await _describe(env, doc)

        last = MAX_SCHEMA_LABELED_COLUMNS - 1
        assert env.gate_calls == [[f"L{i}" for i in range(0, last, 2)]]
        assert descriptions[0] == "index 0 → L0"
        assert descriptions[1] == "index 1 → COLUMN_2"
        assert descriptions[3] == "index 3 → COLUMN_4"
        assert descriptions[last - 1] == f"index {last - 1} → L{last - 1}"
        assert descriptions[last] == f"index {last} → COLUMN_{last + 1}"
        assert descriptions[-1] == "1026 further columns are not described"

    async def test_more_columns_than_the_cap_are_described_up_to_the_cap(
        self, env: _Env
    ) -> None:
        doc = await env.add_document()
        await env.add_schema(doc, _plain_columns(MAX_SCHEMA_LABELED_COLUMNS + 5))

        descriptions = await _describe(env, doc)

        assert len(descriptions) == MAX_SCHEMA_LABELED_COLUMNS + 1
        assert descriptions[MAX_SCHEMA_LABELED_COLUMNS - 1] == (
            f"index {MAX_SCHEMA_LABELED_COLUMNS - 1} → COLUMN_{MAX_SCHEMA_LABELED_COLUMNS}"
        )
        assert descriptions[-1] == "5 further columns are not described"
        assert env.gate_calls == []

    async def test_columns_within_the_cap_have_no_summary(self, env: _Env) -> None:
        doc = await env.add_document()
        await env.add_schema(doc, _plain_columns(MAX_SCHEMA_LABELED_COLUMNS))

        descriptions = await _describe(env, doc)

        assert len(descriptions) == MAX_SCHEMA_LABELED_COLUMNS
        assert all(d.startswith("index ") for d in descriptions)

    async def test_labels_beyond_the_described_columns_are_not_gated_or_shown(
        self, env: _Env
    ) -> None:
        doc = await env.add_document()
        columns = [
            (i, f"COLUMN_{i + 1}", f"L{i}" if i in (1023, 1024, 1030) else None)
            for i in range(MAX_SCHEMA_LABELED_COLUMNS + 7)
        ]
        await env.add_schema(doc, columns)

        descriptions = await _describe(env, doc)

        assert env.gate_calls == [["L1023"]]
        assert descriptions[-2] == "index 1023 → L1023"
        assert descriptions[-1] == "7 further columns are not described"
        assert not any("L1024" in d or "L1030" in d for d in descriptions)

    async def test_schema_without_labels_gates_nothing(self, env: _Env) -> None:
        doc = await env.add_document()
        await env.add_schema(doc, _plain_columns(3))

        descriptions = await _describe(env, doc)

        assert env.gate_calls == []
        assert descriptions == [
            "index 0 → COLUMN_1",
            "index 1 → COLUMN_2",
            "index 2 → COLUMN_3",
        ]

    async def test_legacy_label_is_truncated_before_the_gate(self, env: _Env) -> None:
        doc = await env.add_document()
        await env.add_schema(doc, [(0, "COLUMN_1", "Q" * (MAX_SCHEMA_LABEL_CHARS * 4))])

        await _describe(env, doc)

        assert env.gate_calls == [["Q" * MAX_SCHEMA_LABEL_CHARS]]


class TestGetWithLegacyRows:
    async def test_stored_semantic_label_longer_than_limit_still_renders(
        self, env: _Env
    ) -> None:
        doc = await env.add_document()
        legacy = "L" * (MAX_SCHEMA_LABEL_CHARS * 4)
        await env.add_schema(doc, [(0, "COLUMN_1", legacy)])

        resp = await env.client.get(f"/api/documents/{doc}/schema")

        assert resp.status_code == 200, resp.text
        assert resp.json()["columns"][0]["semantic_label"] == legacy
        assert env.gate_calls == []

    async def test_stored_technical_label_longer_than_limit_still_renders(
        self, env: _Env
    ) -> None:
        doc = await env.add_document()
        legacy = "T" * (MAX_TECHNICAL_LABEL_CHARS * 4)
        await env.add_schema(doc, [(0, legacy, None)])

        resp = await env.client.get(f"/api/documents/{doc}/schema")

        assert resp.status_code == 200, resp.text
        assert resp.json()["columns"][0]["technical_label"] == legacy
