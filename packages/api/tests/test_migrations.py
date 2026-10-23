"""Alembic migrations must build exactly the schema the ORM models declare.

SQLite runs always. PostgreSQL runs when ``SEPTUM_TEST_POSTGRES_URL`` points
at a throwaway database; the test leaves it migrated to head.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import pkgutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from audit_scrub_cases import CAP_VALUES, MASKED_QUERY_ROWS, SCRUB_ROWS, duplicated

import septum_api.models as models_package
from septum_api.database import REGISTERED_MODELS
from septum_api.models import Base

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
