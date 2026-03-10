from __future__ import annotations

"""Database configuration and initialization for Septum."""

import os
from typing import AsyncGenerator, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import Base
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
                image_ocr_languages=_csv_env_to_list(
                    "DEFAULT_OCR_LANGUAGES", default="en"
                ),
                extract_embedded_images=_env_bool(
                    "EXTRACT_EMBEDDED_IMAGES_DEFAULT", True
                ),
                recursive_email_attachments=_env_bool(
                    "RECURSIVE_EMAIL_ATTACHMENTS_DEFAULT", True
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

        # Seed default Non-PII rules (conservative, language-agnostic).
        existing_non_pii = await session.execute(select(NonPiiRule.id))
        has_non_pii = existing_non_pii.first() is not None
        if not has_non_pii:
            # Example: treat isolated exclamation marks as non-PII when misdetected.
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
    default_active_regs_env = os.getenv(
        "DEFAULT_ACTIVE_REGULATIONS", "gdpr"
    ).strip()
    default_active_regulations: List[str] = [
        r.strip().lower() for r in default_active_regs_env.split(",") if r.strip()
    ] or ["gdpr"]

    def is_active(reg_id: str) -> bool:
        return reg_id.lower() in default_active_regulations

    return [
        RegulationRuleset(
            id="gdpr",
            display_name="General Data Protection Regulation",
            region="EU / EEA",
            description="Comprehensive data protection regulation for the European Union and EEA.",
            official_url="https://eur-lex.europa.eu/eli/reg/2016/679/oj",
            entity_types=[
                "PERSON_NAME",
                "EMAIL_ADDRESS",
                "PHONE_NUMBER",
                "IP_ADDRESS",
                "POSTAL_ADDRESS",
                "DATE_OF_BIRTH",
                "NATIONAL_ID",
                "BIOMETRIC_ID",
                "HEALTH_INSURANCE_ID",
                "DIAGNOSIS",
                "MEDICATION",
                "COOKIE_ID",
                "DEVICE_ID",
                "LOCATION",
                "FINANCIAL_ACCOUNT",
                "CREDIT_CARD_NUMBER",
                "BANK_ACCOUNT_NUMBER",
                "TAX_ID",
                "POLITICAL_OPINION",
                "RELIGION",
                "SEXUAL_ORIENTATION",
            ],
            is_builtin=True,
            is_active=is_active("gdpr"),
            custom_notes=None,
        ),
        RegulationRuleset(
            id="hipaa",
            display_name="Health Insurance Portability and Accountability Act",
            region="USA (Healthcare)",
            description="US regulation governing protected health information (PHI).",
            official_url="https://www.hhs.gov/hipaa/index.html",
            entity_types=[
                "PERSON_NAME",
                "DATE_OF_BIRTH",
                "PHONE_NUMBER",
                "EMAIL_ADDRESS",
                "POSTAL_ADDRESS",
                "MEDICAL_RECORD_NUMBER",
                "HEALTH_INSURANCE_ID",
                "DIAGNOSIS",
                "MEDICATION",
                "CLINICAL_NOTE",
                "BIOMETRIC_ID",
            ],
            is_builtin=True,
            is_active=is_active("hipaa"),
            custom_notes=None,
        ),
        RegulationRuleset(
            id="kvkk",
            display_name="Personal Data Protection Law (Turkey)",
            region="Turkey",
            description="Turkish Personal Data Protection Law (KVKK).",
            official_url="https://kvkk.gov.tr/",
            entity_types=[
                "PERSON_NAME",
                "NATIONAL_ID",
                "PHONE_NUMBER",
                "EMAIL_ADDRESS",
                "POSTAL_ADDRESS",
                "BIOMETRIC_ID",
                "HEALTH_INSURANCE_ID",
                "DIAGNOSIS",
                "RELIGION",
                "ETHNICITY",
                "POLITICAL_OPINION",
            ],
            is_builtin=True,
            is_active=is_active("kvkk"),
            custom_notes=None,
        ),
        RegulationRuleset(
            id="lgpd",
            display_name="Lei Geral de Proteção de Dados",
            region="Brazil",
            description="Brazilian General Data Protection Law (LGPD).",
            official_url="https://www.gov.br/escola-national-de-administracao-publica/lgpd",
            entity_types=[
                "PERSON_NAME",
                "CPF",
                "EMAIL_ADDRESS",
                "PHONE_NUMBER",
                "POSTAL_ADDRESS",
                "HEALTH_INSURANCE_ID",
                "DIAGNOSIS",
                "BIOMETRIC_ID",
                "POLITICAL_OPINION",
            ],
            is_builtin=True,
            is_active=is_active("lgpd"),
            custom_notes=None,
        ),
        RegulationRuleset(
            id="ccpa",
            display_name="California Consumer Privacy Act",
            region="USA (California)",
            description="California data protection and privacy regulation.",
            official_url="https://oag.ca.gov/privacy/ccpa",
            entity_types=[
                "PERSON_NAME",
                "EMAIL_ADDRESS",
                "PHONE_NUMBER",
                "POSTAL_ADDRESS",
                "IP_ADDRESS",
                "BIOMETRIC_ID",
                "FINANCIAL_ACCOUNT",
                "DEVICE_ID",
                "COOKIE_ID",
                "COORDINATES",
            ],
            is_builtin=True,
            is_active=is_active("ccpa"),
            custom_notes=None,
        ),
    ]

