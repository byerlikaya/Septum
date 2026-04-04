from __future__ import annotations

"""Database configuration and initialization for Septum.

Supports both SQLite (local dev) and PostgreSQL (production).
When DATABASE_URL is set, PostgreSQL is used via asyncpg.
Otherwise, falls back to SQLite via aiosqlite.
"""

import os
from typing import Any, AsyncGenerator, List

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import Base
from .models.error_log import ErrorLog
from .models.regulation import RegulationRuleset, NonPiiRule
from .models.settings import AppSettings
from .models.chat_session import ChatSession, ChatMessage  # noqa: F401
from .models.user import User  # noqa: F401


DB_PATH_ENV_VAR = "DB_PATH"
DEFAULT_DB_PATH = "./septum.db"


def _build_database_url() -> str:
    """Build the async database URL from environment variables.

    Priority: DATABASE_URL (PostgreSQL) > DB_PATH (SQLite).
    """
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        if database_url.startswith("postgresql://"):
            database_url = database_url.replace(
                "postgresql://", "postgresql+asyncpg://", 1
            )
        return database_url
    db_path = os.getenv(DB_PATH_ENV_VAR, DEFAULT_DB_PATH)
    return f"sqlite+aiosqlite:///{db_path}"


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


DATABASE_URL = _build_database_url()

engine = create_async_engine(DATABASE_URL, **_engine_kwargs(DATABASE_URL))
async_session_maker = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


def is_sqlite() -> bool:
    """Return True if the current database backend is SQLite."""
    return "sqlite" in DATABASE_URL


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session."""
    async with async_session_maker() as session:
        yield session


async def init_db() -> None:
    """Create all tables and seed default application settings and regulations.

    For SQLite: uses create_all (development convenience).
    For PostgreSQL: tables should be managed by Alembic; this only seeds defaults.
    """
    if is_sqlite():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await _sqlite_ensure_columns()

    await _seed_defaults()


async def _sqlite_ensure_columns() -> None:
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
    ]

    async with engine.begin() as conn:
        for table, ddl in migrations:
            cols = await conn.execute(text(f"PRAGMA table_info({table})"))
            col_name = ddl.split("ADD COLUMN ")[1].split()[0]
            existing = {row[1] for row in cols}
            if col_name not in existing:
                await conn.execute(text(ddl))


async def _seed_defaults() -> None:
    """Seed default AppSettings and built-in RegulationRuleset entries."""
    from sqlalchemy import func

    async with async_session_maker() as session:
        settings_result = await session.execute(
            select(AppSettings).where(AppSettings.id == 1)
        )
        settings = settings_result.scalar_one_or_none()

        if settings is None:
            default_active_regs_env = os.getenv(
                "DEFAULT_ACTIVE_REGULATIONS", "gdpr"
            ).strip()
            default_active_regulations: List[str] = [
                r.strip().lower()
                for r in default_active_regs_env.split(",")
                if r.strip()
            ] or ["gdpr"]

            settings = AppSettings(
                id=1,
                llm_provider=os.getenv("LLM_PROVIDER", "anthropic"),
                llm_model=os.getenv("LLM_MODEL", "claude-3-5-sonnet-latest"),
                ollama_base_url=os.getenv(
                    "OLLAMA_BASE_URL", "http://localhost:11434"
                ),
                ollama_chat_model=os.getenv(
                    "OLLAMA_CHAT_MODEL", "llama3.2:3b"
                ),
                ollama_deanon_model=os.getenv(
                    "OLLAMA_DEANON_MODEL", "llama3.2:3b"
                ),
                deanon_enabled=_env_bool("DEANON_ENABLED_DEFAULT", True),
                deanon_strategy=os.getenv("DEANON_STRATEGY", "simple"),
                require_approval=_env_bool("REQUIRE_APPROVAL_DEFAULT", False),
                show_json_output=_env_bool("SHOW_JSON_OUTPUT_DEFAULT", False),
                use_presidio_layer=_env_bool("USE_PRESIDIO_LAYER_DEFAULT", True),
                use_ner_layer=_env_bool("USE_NER_LAYER_DEFAULT", True),
                use_ollama_validation_layer=_env_bool("USE_OLLAMA_VALIDATION_LAYER_DEFAULT", True),
                use_ollama_layer=_env_bool("USE_OLLAMA_LAYER_DEFAULT", False),
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
                    "DEFAULT_OCR_LANGUAGES", default="en"
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

        # Seed minimal Non-PII rules (language-agnostic examples only).
        # Users should define language-specific rules through the UI/API.
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
