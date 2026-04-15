from __future__ import annotations

"""Database configuration and initialization for Septum.

Supports both SQLite (local dev) and PostgreSQL (production).
The engine is created lazily — either by the setup wizard (first run)
or automatically at startup when ``config.json`` already has database
configuration.
"""

import logging
import os
from typing import Any, AsyncGenerator, List

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import default_ollama_url
from .models import Base
from .models.api_key import ApiKey  # noqa: F401
from .models.chat_session import ChatMessage, ChatSession  # noqa: F401
from .models.document import Chunk, Document  # noqa: F401
from .models.entity_detection import EntityDetection  # noqa: F401
from .models.regulation import NonPiiRule, RegulationRuleset
from .models.settings import AppSettings
from .models.user import User  # noqa: F401

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy engine — initialised by ``initialize_engine()``
# ---------------------------------------------------------------------------
_engine: AsyncEngine | None = None
_session_maker: async_sessionmaker[AsyncSession] | None = None


def _sqlite_wal_connect(dbapi_conn: Any, _record: Any) -> None:
    """Enable WAL journal mode and a generous busy timeout for SQLite.

    WAL lets readers and a single writer run concurrently. The 30s
    busy timeout absorbs spikes when several background ingestion tasks
    flush chunk and entity rows for parallel uploads at the same time.
    """
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def _engine_kwargs(url: str) -> dict[str, Any]:
    """Return dialect-specific engine keyword arguments."""
    if "postgresql" in url:
        return {
            "echo": False,
            "future": True,
            "pool_size": 10,
            "max_overflow": 20,
            "pool_pre_ping": True,
        }
    return {"echo": False, "future": True}


def build_database_url(database_url: str = "", db_path: str = "") -> str:
    """Build an async-compatible database URL.

    Falls back to environment variables for backwards compatibility with
    scripts that do not use the bootstrap layer.
    """
    url = database_url or os.getenv("DATABASE_URL", "")
    if url:
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url
    path = db_path or os.getenv("DB_PATH", "./septum.db")
    return f"sqlite+aiosqlite:///{path}"


def initialize_engine(database_url: str) -> None:
    """Create the global async engine and session maker.

    Called once — either from the application lifespan handler (when
    ``config.json`` already contains database configuration) or from the
    setup wizard after the user selects a database.
    """
    global _engine, _session_maker
    if _engine is not None:
        logger.info("Re-initialising database engine")

    # Ensure the parent directory exists for SQLite databases.
    if "sqlite" in database_url:
        from pathlib import Path
        # sqlite+aiosqlite:///path → strip scheme prefix
        raw_path = database_url.split("///", 1)[-1] if "///" in database_url else ""
        if raw_path:
            Path(raw_path).parent.mkdir(parents=True, exist_ok=True)

    _engine = create_async_engine(database_url, **_engine_kwargs(database_url))
    if "sqlite" in database_url:
        from sqlalchemy import event
        event.listen(_engine.sync_engine, "connect", _sqlite_wal_connect)
    _session_maker = async_sessionmaker(
        bind=_engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )
    logger.info("Database engine initialised (%s)", "PostgreSQL" if "postgresql" in database_url else "SQLite")


def engine_is_ready() -> bool:
    """Return True if the database engine has been initialised."""
    return _engine is not None


def get_engine() -> AsyncEngine:
    """Return the global async engine, raising if not yet initialised."""
    if _engine is None:
        raise RuntimeError("Database engine not initialised — call initialize_engine() first")
    return _engine


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    """Return the global session maker, raising if not yet initialised."""
    if _session_maker is None:
        raise RuntimeError("Database session maker not initialised")
    return _session_maker


def is_sqlite() -> bool:
    """Return True if the current database backend is SQLite."""
    if _engine is None:
        return True
    return "sqlite" in str(_engine.url)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session."""
    if _session_maker is None:
        raise HTTPException(503, "Database not configured yet")
    async with _session_maker() as session:
        yield session


async def init_db() -> None:
    """Create all tables and seed default application settings and regulations.

    For SQLite: uses ``create_all`` (development convenience).
    For PostgreSQL: tables should be managed by Alembic; this only seeds defaults.
    """
    eng = get_engine()
    sm = get_session_maker()

    if is_sqlite():
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await _sqlite_ensure_columns(eng)

    await _seed_defaults(sm)


async def _sqlite_ensure_columns(eng: AsyncEngine) -> None:
    """Add columns that may be missing in an existing SQLite database.

    ``create_all`` only creates *new* tables; it never ALTERs existing ones.
    This helper inspects ``PRAGMA table_info`` and runs ``ALTER TABLE`` for
    any column the ORM expects but SQLite doesn't have yet.
    """
    migrations: list[tuple[str, str]] = [
        (
            "app_settings",
            "ALTER TABLE app_settings ADD COLUMN setup_completed BOOLEAN NOT NULL DEFAULT 0",
        ),
        (
            "documents",
            "ALTER TABLE documents ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE",
        ),
        (
            "chat_sessions",
            "ALTER TABLE chat_sessions ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE",
        ),
        (
            "users",
            "ALTER TABLE users ADD COLUMN role VARCHAR NOT NULL DEFAULT 'editor'",
        ),
        (
            "app_settings",
            "ALTER TABLE app_settings ADD COLUMN use_ollama_semantic_layer BOOLEAN NOT NULL DEFAULT 0",
        ),
        (
            "app_settings",
            "ALTER TABLE app_settings ADD COLUMN approval_timeout_seconds INTEGER NOT NULL DEFAULT 300",
        ),
    ]

    async with eng.begin() as conn:
        for table, ddl in migrations:
            cols = await conn.execute(text(f"PRAGMA table_info({table})"))
            col_name = ddl.split("ADD COLUMN ")[1].split()[0]
            existing = {row[1] for row in cols}
            if col_name not in existing:
                await conn.execute(text(ddl))


def build_default_app_settings() -> AppSettings:
    """Construct an unsaved :class:`AppSettings` with env-driven defaults.

    Single source of truth for the default row — consumed by both the
    startup ``_seed_defaults`` path and the lazy recovery path in
    ``utils.db_helpers.load_settings`` so fresh databases and partial
    bootstraps produce the same default configuration.
    """
    default_active_regs_env = os.getenv(
        "DEFAULT_ACTIVE_REGULATIONS", "gdpr"
    ).strip()
    default_active_regulations: List[str] = [
        r.strip().lower()
        for r in default_active_regs_env.split(",")
        if r.strip()
    ] or ["gdpr"]

    return AppSettings(
        id=1,
        llm_provider=os.getenv("LLM_PROVIDER", "anthropic"),
        llm_model=os.getenv("LLM_MODEL", "claude-sonnet-4-20250514"),
        ollama_base_url=os.getenv(
            "OLLAMA_BASE_URL", default_ollama_url()
        ),
        ollama_chat_model=os.getenv(
            "OLLAMA_CHAT_MODEL", "llama3.2:3b"
        ),
        ollama_deanon_model=os.getenv(
            "OLLAMA_DEANON_MODEL", "llama3.2:3b"
        ),
        deanon_enabled=_env_bool("DEANON_ENABLED_DEFAULT", True),
        deanon_strategy=os.getenv("DEANON_STRATEGY", "simple"),
        require_approval=_env_bool("REQUIRE_APPROVAL_DEFAULT", True),
        approval_timeout_seconds=_env_int(
            "APPROVAL_TIMEOUT_SECONDS_DEFAULT", 300
        ),
        show_json_output=_env_bool("SHOW_JSON_OUTPUT_DEFAULT", False),
        use_presidio_layer=_env_bool("USE_PRESIDIO_LAYER_DEFAULT", True),
        use_ner_layer=_env_bool("USE_NER_LAYER_DEFAULT", True),
        use_ollama_validation_layer=_env_bool(
            "USE_OLLAMA_VALIDATION_LAYER_DEFAULT", True
        ),
        use_ollama_layer=_env_bool("USE_OLLAMA_LAYER_DEFAULT", True),
        use_ollama_semantic_layer=_env_bool(
            "USE_OLLAMA_SEMANTIC_LAYER_DEFAULT", False
        ),
        chunk_size=_env_int("CHUNK_SIZE_DEFAULT", 800),
        chunk_overlap=_env_int("CHUNK_OVERLAP_DEFAULT", 200),
        top_k_retrieval=_env_int("TOP_K_RETRIEVAL_DEFAULT", 5),
        pdf_chunk_size=_env_int("PDF_CHUNK_SIZE_DEFAULT", 1200),
        audio_chunk_size=_env_int("AUDIO_CHUNK_SIZE_DEFAULT", 60),
        spreadsheet_chunk_size=_env_int(
            "SPREADSHEET_CHUNK_SIZE_DEFAULT", 200
        ),
        whisper_model=os.getenv("WHISPER_MODEL", "base"),
        default_audio_language=os.getenv("DEFAULT_AUDIO_LANGUAGE") or None,
        image_ocr_languages=_csv_env_to_list(
            "DEFAULT_OCR_LANGUAGES", default="en,tr,de,ru,fr"
        ),
        ocr_provider=os.getenv("OCR_PROVIDER", "paddleocr").strip().lower()
        or "paddleocr",
        ocr_provider_options=None,
        extract_embedded_images=_env_bool(
            "EXTRACT_EMBEDDED_IMAGES_DEFAULT", True
        ),
        recursive_email_attachments=_env_bool(
            "RECURSIVE_EMAIL_ATTACHMENTS_DEFAULT",
            True,
        ),
        default_active_regulations=default_active_regulations,
        setup_completed=False,
    )


async def _seed_defaults(sm: async_sessionmaker[AsyncSession]) -> None:
    """Seed default AppSettings and built-in RegulationRuleset entries."""

    async with sm() as session:
        settings_result = await session.execute(
            select(AppSettings).where(AppSettings.id == 1)
        )
        settings = settings_result.scalar_one_or_none()

        if settings is None:
            settings = build_default_app_settings()
            session.add(settings)

        existing_regs = await session.execute(
            select(RegulationRuleset.id)
            .where(RegulationRuleset.is_builtin.is_(True))
            .order_by(RegulationRuleset.id)
        )
        existing_ids = {row[0] for row in existing_regs.all()}

        builtin_regs = _builtin_regulations()
        for reg in builtin_regs:
            if reg.id not in existing_ids:
                session.add(reg)

        await session.execute(
            text(
                "UPDATE app_settings SET ocr_provider = 'paddleocr' "
                "WHERE id = 1 AND (ocr_provider IS NULL OR ocr_provider = '')"
            )
        )

        existing_non_pii = await session.execute(select(NonPiiRule.id))
        has_non_pii = existing_non_pii.first() is not None
        if not has_non_pii:
            session.add(
                NonPiiRule(
                    pattern_type="token",
                    pattern="!",
                    languages=[],
                    entity_types=[],
                    min_score=None,
                    is_active=True,
                )
            )

        await session.commit()


def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean environment variable with a default."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    """Parse an integer environment variable with a default."""
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _csv_env_to_list(name: str, default: str | None = None) -> list[str]:
    """Parse a comma-separated environment variable into a list of strings."""
    value = os.getenv(name)
    if value is None or not value.strip():
        if default is None:
            return []
        return [default]
    return [item.strip() for item in value.split(",") if item.strip()]


def _builtin_regulations() -> list[RegulationRuleset]:
    """Return the built-in regulation rulesets to seed into the database."""
    from .seeds.regulations import builtin_regulations
    return builtin_regulations()
