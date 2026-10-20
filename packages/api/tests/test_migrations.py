"""Alembic migrations must build exactly the schema the ORM models declare.

SQLite runs always. PostgreSQL runs when ``SEPTUM_TEST_POSTGRES_URL`` points
at a throwaway database; the test leaves it migrated to head.
"""

from __future__ import annotations

import importlib
import os
import pkgutil
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

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
