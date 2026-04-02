from __future__ import annotations

"""Database configuration and initialization for Septum."""

import os
from typing import Any, AsyncGenerator, List

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import Base
from .models.error_log import ErrorLog
from .models.regulation import RegulationRuleset, NonPiiRule
from .models.settings import AppSettings


DB_PATH_ENV_VAR = "DB_PATH"
DEFAULT_DB_PATH = "./septum.db"


def _build_database_url() -> str:
    """Build the async SQLite database URL from environment variables."""
    db_path = os.getenv(DB_PATH_ENV_VAR, DEFAULT_DB_PATH)
    return f"sqlite+aiosqlite:///{db_path}"


DATABASE_URL = _build_database_url()

engine = create_async_engine(DATABASE_URL, echo=False, future=True)
async_session_maker = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session."""
    async with async_session_maker() as session:
        yield session


async def init_db() -> None:
    """Create all tables and seed default application settings and regulations.

    Model metadata must be fully populated (via imports) before create_all.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_ocr_provider_columns)
        await conn.run_sync(_ensure_ner_overrides_column)
        await conn.run_sync(_ensure_default_audio_language_column)

    await _seed_defaults()


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


def _ensure_ocr_provider_columns(conn: Any) -> None:
    """Add ocr_provider and ocr_provider_options columns if missing (e.g. existing DBs)."""
    for sql in (
        "ALTER TABLE app_settings ADD COLUMN ocr_provider TEXT DEFAULT 'paddleocr'",
        "ALTER TABLE app_settings ADD COLUMN ocr_provider_options TEXT",
    ):
        try:
            conn.execute(text(sql))
        except Exception as e:
            if "duplicate column name" not in str(e).lower():
                raise


def _ensure_ner_overrides_column(conn: Any) -> None:
    """Add ner_model_overrides column if missing (e.g. existing DBs)."""
    try:
        conn.execute(text("ALTER TABLE app_settings ADD COLUMN ner_model_overrides TEXT"))
    except Exception as e:
        if "duplicate column name" not in str(e).lower():
            raise


def _ensure_default_audio_language_column(conn: Any) -> None:
    """Add default_audio_language column if missing (e.g. existing DBs)."""
    try:
        conn.execute(
            text("ALTER TABLE app_settings ADD COLUMN default_audio_language TEXT")
        )
    except Exception as e:
        if "duplicate column name" not in str(e).lower():
            raise


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

