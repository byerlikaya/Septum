"""Alembic migrations must build exactly the schema the ORM models declare.

SQLite runs always. PostgreSQL runs when ``SEPTUM_TEST_POSTGRES_URL`` points
at a throwaway database; the test leaves it migrated to head.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import os
import pkgutil
import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator

import pytest
import spreadsheet_dedupe_cases as dedupe_cases
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from audit_scrub_cases import CAP_VALUES, MASKED_QUERY_ROWS, SCRUB_ROWS, duplicated
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

import septum_api.database as database
import septum_api.models as models_package
from septum_api.database import REGISTERED_MODELS
from septum_api.models import Base
from septum_api.request_limits import MAX_SCHEMA_LABELED_COLUMNS

_API_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    """Build a config without an ini file so Alembic leaves logging untouched."""
    cfg = Config()
    cfg.set_main_option("script_location", str(_API_ROOT / "alembic"))
    return cfg


def _round_trip(cfg: Config) -> None:
    command.upgrade(cfg, "head")
    command.check(cfg)
    command.downgrade(cfg, "-1")
    command.upgrade(cfg, "head")
    command.check(cfg)


def test_sqlite_migrations_match_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'migrated.db'}")
    _round_trip(_alembic_config())


def test_revision_016_is_noop_on_create_all_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "create_all.db"
    engine = sa.create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO users (email, hashed_password, is_active, created_at, role) "
                "VALUES ('owner@example.com', 'x', 1, '2026-01-01', 'admin')"
            )
        )

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg = _alembic_config()
    command.stamp(cfg, "015")
    command.upgrade(cfg, "head")
    command.check(cfg)

    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT COUNT(*) FROM users")).scalar_one() == 1
    engine.dispose()


def test_revision_017_adds_nullable_error_code_to_existing_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "at_016.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg = _alembic_config()
    command.upgrade(cfg, "016")
    engine = sa.create_engine(f"sqlite:///{db_path}")
    before = {c["name"] for c in sa.inspect(engine).get_columns("documents")}

    command.upgrade(cfg, "017")

    columns = {c["name"]: c for c in sa.inspect(engine).get_columns("documents")}
    engine.dispose()
    assert "ingestion_error_code" not in before
    assert columns["ingestion_error_code"]["nullable"] is True


def test_revision_018_adds_nullable_entity_types_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "at_017.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg = _alembic_config()
    command.upgrade(cfg, "017")
    engine = sa.create_engine(f"sqlite:///{db_path}")
    before = {c["name"] for c in sa.inspect(engine).get_columns("documents")}

    command.upgrade(cfg, "018")
    columns = {c["name"]: c for c in sa.inspect(engine).get_columns("documents")}

    command.stamp(cfg, "017")
    command.upgrade(cfg, "018")
    command.upgrade(cfg, "head")
    command.check(cfg)
    after_rerun = [c["name"] for c in sa.inspect(engine).get_columns("documents")]

    command.downgrade(cfg, "017")
    after_downgrade = {c["name"] for c in sa.inspect(engine).get_columns("documents")}
    engine.dispose()

    assert "sanitized_entity_types" not in before
    assert columns["sanitized_entity_types"]["nullable"] is True
    assert after_rerun.count("sanitized_entity_types") == 1
    assert "sanitized_entity_types" not in after_downgrade


def test_revision_019_adds_nullable_policy_digest_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "at_018.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg = _alembic_config()
    command.upgrade(cfg, "018")
    engine = sa.create_engine(f"sqlite:///{db_path}")
    before = {c["name"] for c in sa.inspect(engine).get_columns("documents")}

    command.upgrade(cfg, "019")
    columns = {c["name"]: c for c in sa.inspect(engine).get_columns("documents")}

    command.stamp(cfg, "018")
    command.upgrade(cfg, "019")
    command.upgrade(cfg, "head")
    command.check(cfg)
    after_rerun = [c["name"] for c in sa.inspect(engine).get_columns("documents")]

    command.downgrade(cfg, "018")
    after_downgrade = {c["name"] for c in sa.inspect(engine).get_columns("documents")}
    engine.dispose()

    assert "sanitized_policy_digest" not in before
    assert columns["sanitized_policy_digest"]["nullable"] is True
    assert after_rerun.count("sanitized_policy_digest") == 1
    assert "sanitized_policy_digest" not in after_downgrade


def test_revision_020_adds_nullable_sanitized_language_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "at_019.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg = _alembic_config()
    command.upgrade(cfg, "019")
    engine = sa.create_engine(f"sqlite:///{db_path}")
    before = {c["name"] for c in sa.inspect(engine).get_columns("documents")}

    command.upgrade(cfg, "020")
    columns = {c["name"]: c for c in sa.inspect(engine).get_columns("documents")}

    command.stamp(cfg, "019")
    command.upgrade(cfg, "020")
    command.upgrade(cfg, "head")
    command.check(cfg)
    after_rerun = [c["name"] for c in sa.inspect(engine).get_columns("documents")]

    command.downgrade(cfg, "019")
    after_downgrade = {c["name"] for c in sa.inspect(engine).get_columns("documents")}
    engine.dispose()

    assert "sanitized_language" not in before
    assert columns["sanitized_language"]["nullable"] is True
    assert after_rerun.count("sanitized_language") == 1
    assert "sanitized_language" not in after_downgrade


def test_revision_021_replaces_single_column_document_index_and_is_reversible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "at_020.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg = _alembic_config()
    command.upgrade(cfg, "020")
    engine = sa.create_engine(f"sqlite:///{db_path}")

    def indexes() -> dict[str, list[str]]:
        return {i["name"]: i["column_names"] for i in sa.inspect(engine).get_indexes("audit_events")}

    before = indexes()

    command.upgrade(cfg, "021")
    after_upgrade = indexes()

    command.stamp(cfg, "020")
    command.upgrade(cfg, "021")
    command.upgrade(cfg, "head")
    command.check(cfg)
    after_rerun = indexes()

    command.downgrade(cfg, "020")
    after_downgrade = indexes()

    command.upgrade(cfg, "head")
    command.check(cfg)
    after_reupgrade = indexes()
    engine.dispose()

    single, composite = "ix_audit_events_document_id", "ix_audit_events_document_event_id"
    composite_columns = ["document_id", "event_type", "id"]
    assert single in before and composite not in before
    assert single not in after_upgrade and after_upgrade[composite] == composite_columns
    assert single not in after_rerun and after_rerun[composite] == composite_columns
    assert after_downgrade[single] == ["document_id"] and composite not in after_downgrade
    assert single not in after_reupgrade and after_reupgrade[composite] == composite_columns


def _insert_audit_extras(conn: sa.Connection, extras: list[str | None]) -> None:
    for extra in extras:
        conn.execute(
            sa.text(
                "INSERT INTO audit_events (created_at, event_type, regulation_ids, "
                "entity_types_detected, entity_count, extra) "
                "VALUES ('2026-01-01', 'pii_detected', '[]', '{}', 0, :extra)"
            ),
            {"extra": extra},
        )


def _audit_extras(conn: sa.Connection) -> list[dict | None]:
    rows = conn.execute(sa.text("SELECT extra FROM audit_events ORDER BY id")).scalars()
    return [None if raw is None else json.loads(raw) for raw in rows]


def test_revision_022_scrubs_audit_document_names_idempotently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "at_021.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg = _alembic_config()
    command.upgrade(cfg, "021")
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        _insert_audit_extras(conn, [
            json.dumps({"document_name": "Jane Doe CV.pdf", "source": "chat_query", "masked_query": "q"}),
            json.dumps({"document_name": None, "strategy": "simple"}),
            json.dumps({"strategy": "simple"}),
            None,
        ])
    scrubbed = [{"source": "chat_query", "masked_query": "q"}, {"strategy": "simple"}, {"strategy": "simple"}, None]

    command.upgrade(cfg, "022")
    with engine.connect() as conn:
        after_upgrade = _audit_extras(conn)
    command.stamp(cfg, "021")
    command.upgrade(cfg, "022")
    command.downgrade(cfg, "021")
    with engine.connect() as conn:
        after_downgrade = _audit_extras(conn)
    command.upgrade(cfg, "head")
    command.check(cfg)
    with engine.connect() as conn:
        after_reupgrade = _audit_extras(conn)
    engine.dispose()

    assert after_upgrade == scrubbed
    assert after_downgrade == scrubbed
    # Head includes 023, which removes masked_query too.
    assert after_reupgrade == [{"source": "chat_query"}, *scrubbed[1:]]


def test_revision_022_matches_the_shared_cases_on_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [(stored, sqlite) for stored, _, sqlite in SCRUB_ROWS] + [("not json", "not json")]
    db_path = tmp_path / "at_021_cases.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg = _alembic_config()
    command.upgrade(cfg, "021")
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        _insert_audit_extras(conn, [stored for stored, _ in rows])

    def stored() -> list[str]:
        with engine.connect() as conn:
            return conn.execute(sa.text("SELECT extra FROM audit_events ORDER BY id")).scalars().all()

    command.upgrade(cfg, "022")
    after_first = stored()
    command.stamp(cfg, "021")
    command.upgrade(cfg, "022")
    after_rerun = stored()
    engine.dispose()

    assert after_first == [expected for _, expected in rows]
    assert after_rerun == after_first


_CAP_REVISIONS = pytest.mark.parametrize(
    ("previous", "filename", "key"),
    [
        ("021", "022_scrub_audit_events_document_name.py", "document_name"),
        ("022", "023_scrub_audit_events_masked_query.py", "masked_query"),
    ],
)


def _capped_revision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, previous: str, filename: str):
    """Migrate a fresh SQLite database to ``previous``; load ``filename`` with its cap at 2."""
    db_path = tmp_path / f"at_{previous}_cap.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    command.upgrade(_alembic_config(), previous)
    spec = importlib.util.spec_from_file_location(filename, _API_ROOT / "alembic" / "versions" / filename)
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    monkeypatch.setattr(revision, "_SQLITE_MAX_PASSES", 2)
    return revision, sa.create_engine(f"sqlite:///{db_path}")


@_CAP_REVISIONS
def test_revision_sqlite_removes_duplicates_up_to_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, previous: str, filename: str, key: str
) -> None:
    revision, engine = _capped_revision(tmp_path, monkeypatch, previous, filename)
    with engine.begin() as conn:
        _insert_audit_extras(conn, [duplicated(key, 2)])
        monkeypatch.setattr(revision, "op", SimpleNamespace(get_bind=lambda: conn))
        revision.upgrade()
        stored = conn.execute(sa.text("SELECT extra FROM audit_events")).scalar_one()
    engine.dispose()

    assert stored == '{"k":1}'


@_CAP_REVISIONS
def test_revision_sqlite_raises_past_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, previous: str, filename: str, key: str
) -> None:
    revision, engine = _capped_revision(tmp_path, monkeypatch, previous, filename)
    with engine.begin() as conn:
        _insert_audit_extras(conn, [duplicated(key, 3)])
        monkeypatch.setattr(revision, "op", SimpleNamespace(get_bind=lambda: conn))
        with pytest.raises(RuntimeError) as raised:
            revision.upgrade()
    engine.dispose()

    message = str(raised.value)
    assert "re-run the upgrade" in message
    assert not any(value in message for value in CAP_VALUES)


def test_revision_023_matches_the_shared_cases_on_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rows stored before 022, then 022 and 023 run, as on a real upgrade."""
    rows = [(stored, sqlite) for stored, _, sqlite in MASKED_QUERY_ROWS] + [("not json", "not json")]
    db_path = tmp_path / "at_021_cases_023.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg = _alembic_config()
    command.upgrade(cfg, "021")
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        _insert_audit_extras(conn, [stored for stored, _ in rows])

    def stored() -> list[str]:
        with engine.connect() as conn:
            return conn.execute(sa.text("SELECT extra FROM audit_events ORDER BY id")).scalars().all()

    command.upgrade(cfg, "023")
    after_first = stored()
    command.stamp(cfg, "022")
    command.upgrade(cfg, "023")
    after_rerun = stored()
    command.downgrade(cfg, "022")
    after_downgrade = stored()
    command.upgrade(cfg, "head")
    command.check(cfg)
    engine.dispose()

    assert after_first == [expected for _, expected in rows]
    assert after_rerun == after_first
    assert after_downgrade == after_first


# Runs a sync callable on a connection inside a transaction and returns its result.
Run = Callable[[Callable[[sa.Connection], Any]], Any]


def _assert_dedupe_cycle(cfg: Config, run: Run, caplog: pytest.LogCaptureFixture) -> None:
    """Seed duplicates at 023, upgrade, re-run, enforce, downgrade, upgrade again."""
    command.upgrade(cfg, "023")
    run(dedupe_cases.seed)

    with caplog.at_level("WARNING", logger="alembic.runtime.migration"):
        command.upgrade(cfg, "024")
    after_upgrade = run(dedupe_cases.stored)
    schemas_after_upgrade = run(dedupe_cases.schema_count)
    indexes_after_upgrade = run(dedupe_cases.spreadsheet_indexes)

    command.stamp(cfg, "023")
    command.upgrade(cfg, "024")
    after_rerun = run(dedupe_cases.stored)

    with pytest.raises(sa.exc.IntegrityError):
        run(lambda conn: dedupe_cases.insert_schemas(conn, [(99, 1)]))
    with pytest.raises(sa.exc.IntegrityError):
        run(lambda conn: dedupe_cases.insert_columns(conn, [(None, 1, 0, None, None)]))

    command.downgrade(cfg, "023")
    indexes_after_downgrade = run(dedupe_cases.spreadsheet_indexes)
    after_downgrade = run(dedupe_cases.stored)
    command.upgrade(cfg, "head")
    command.check(cfg)
    indexes_after_reupgrade = run(dedupe_cases.spreadsheet_indexes)

    assert after_upgrade == dedupe_cases.EXPECTED
    assert schemas_after_upgrade == 3
    assert after_rerun == after_upgrade
    assert after_downgrade == after_upgrade
    assert dedupe_cases.EXPECTED_LOG in caplog.text
    assert indexes_after_upgrade == dedupe_cases.UNIQUE_INDEXES
    assert indexes_after_downgrade == dedupe_cases.SUPERSEDED_INDEXES
    assert indexes_after_reupgrade == dedupe_cases.UNIQUE_INDEXES


def _sqlite_runner(engine: sa.Engine) -> Run:
    def run(fn: Callable[[sa.Connection], Any]) -> Any:
        with engine.begin() as conn:
            return fn(conn)

    return run


@pytest.fixture(scope="module")
def sqlite_at_023(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A SQLite database migrated to 023 once, copied by each test that needs one."""
    path = tmp_path_factory.mktemp("template") / "at_023.db"
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path}")
        command.upgrade(_alembic_config(), "023")
    return path


@pytest.fixture
def at_023(
    sqlite_at_023: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[Config, sa.Engine]]:
    """Alembic config and engine of a fresh copy of the 023 template."""
    db_path = tmp_path / "at_023.db"
    shutil.copyfile(sqlite_at_023, db_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        yield _alembic_config(), engine
    finally:
        engine.dispose()


def test_revision_024_dedupes_spreadsheet_schemas_and_enforces_uniqueness_on_sqlite(
    at_023: tuple[Config, sa.Engine], caplog: pytest.LogCaptureFixture
) -> None:
    cfg, engine = at_023
    _assert_dedupe_cycle(cfg, _sqlite_runner(engine), caplog)


def test_revision_024_counts_documents_a_merge_leaves_over_the_label_budget(
    at_023: tuple[Config, sa.Engine], caplog: pytest.LogCaptureFixture
) -> None:
    cfg, engine = at_023
    with engine.begin() as conn:
        dedupe_cases.insert_schemas(conn, [(1, 1), (2, 1)])
        dedupe_cases.insert_columns(
            conn, [(None, 1 + i // 600, i, f"L{i}", None) for i in range(1200)]
        )

    with caplog.at_level("WARNING", logger="alembic.runtime.migration"):
        command.upgrade(cfg, "024")
    with engine.connect() as conn:
        labelled = conn.execute(
            sa.text("SELECT COUNT(*) FROM spreadsheet_columns WHERE schema_id = 1")
        ).scalar_one()

    assert labelled == 1200
    assert (
        "moved 600 columns, deleted 1 duplicate schemas and 0 duplicate columns; "
        "1 documents exceed the labelled column budget"
    ) in caplog.text


def test_revision_024_is_noop_on_a_database_without_duplicates(
    at_023: tuple[Config, sa.Engine], caplog: pytest.LogCaptureFixture
) -> None:
    cfg, engine = at_023
    with engine.begin() as conn:
        dedupe_cases.insert_schemas(conn, [(1, 1)])
        dedupe_cases.insert_columns(conn, [(None, 1, 0, "Salary", None), (None, 1, 1, None, None)])
        before = dedupe_cases.stored(conn)

    with caplog.at_level("WARNING", logger="alembic.runtime.migration"):
        command.upgrade(cfg, "head")
    with engine.connect() as conn:
        after = dedupe_cases.stored(conn)

    assert after == before
    assert "Spreadsheet schema dedupe" not in caplog.text


def _whitespace_normalised(statement: str) -> str:
    return " ".join(statement.split())


def test_sqlite_startup_dedupe_matches_revision_024() -> None:
    """The two copies (revisions stay frozen) must not drift apart."""
    filename = "024_unique_spreadsheet_schema_rows.py"
    spec = importlib.util.spec_from_file_location(filename, _API_ROOT / "alembic" / "versions" / filename)
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)

    assert [_whitespace_normalised(q) for q in database._SPREADSHEET_DEDUPE_STATEMENTS] == [
        _whitespace_normalised(q) for q in revision._DEDUPE_STATEMENTS
    ]
    assert _whitespace_normalised(database._SPREADSHEET_OVER_LABEL_BUDGET) == _whitespace_normalised(
        revision._OVER_LABEL_BUDGET
    )
    assert revision._LABEL_BUDGET == MAX_SCHEMA_LABELED_COLUMNS


async def _postgres_run(url: sa.URL, fn: Callable[[sa.Connection], Any], **engine_kwargs: Any) -> Any:
    engine = create_async_engine(url, **engine_kwargs)
    try:
        async with engine.begin() as conn:
            return await conn.run_sync(fn)
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    not os.getenv("SEPTUM_TEST_POSTGRES_URL"),
    reason="SEPTUM_TEST_POSTGRES_URL not set",
)
def test_revision_024_dedupes_spreadsheet_schemas_on_postgres(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Runs in its own database, so the shared one stays at head."""
    base = make_url(os.environ["SEPTUM_TEST_POSTGRES_URL"])
    name = f"dedupe_{uuid.uuid4().hex[:12]}"

    def _admin(statement: str) -> None:
        asyncio.run(
            _postgres_run(
                base, lambda conn: conn.execute(sa.text(statement)), isolation_level="AUTOCOMMIT"
            )
        )

    _admin(f'CREATE DATABASE "{name}"')
    try:
        url = base.set(database=name)
        monkeypatch.setenv("DATABASE_URL", url.render_as_string(hide_password=False))
        _assert_dedupe_cycle(
            _alembic_config(), lambda fn: asyncio.run(_postgres_run(url, fn)), caplog
        )
    finally:
        _admin(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.mark.skipif(
    not os.getenv("SEPTUM_TEST_POSTGRES_URL"),
    reason="SEPTUM_TEST_POSTGRES_URL not set",
)
def test_postgres_migrations_match_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", os.environ["SEPTUM_TEST_POSTGRES_URL"])
    _round_trip(_alembic_config())


def test_every_model_module_is_registered() -> None:
    for module in pkgutil.iter_modules(models_package.__path__):
        importlib.import_module(f"{models_package.__name__}.{module.name}")
    registered = {model.__table__.name for model in REGISTERED_MODELS}
    assert set(Base.metadata.tables) == registered
