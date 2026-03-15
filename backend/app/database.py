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
                ocr_provider=os.getenv("OCR_PROVIDER", "easyocr").strip().lower()
                or "easyocr",
                ocr_provider_options=None,
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

        # Seed minimal Non-PII rules (language-agnostic examples only).
        # Users should define language-specific rules through the UI/API.
        await session.execute(
            text(
                "UPDATE app_settings SET ocr_provider = 'easyocr' "
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
        "ALTER TABLE app_settings ADD COLUMN ocr_provider TEXT DEFAULT 'easyocr'",
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
            description="Regulation (EU) 2016/679. Personal data: Art. 4(1). Special categories: Art. 9(1). Online identifiers: Rec. 30.",
            official_url="https://eur-lex.europa.eu/eli/reg/2016/679/oj",
            entity_types=[
                "PERSON_NAME",
                "FIRST_NAME",
                "LAST_NAME",
                "EMAIL_ADDRESS",
                "PHONE_NUMBER",
                "IP_ADDRESS",
                "MAC_ADDRESS",
                "POSTAL_ADDRESS",
                "STREET_ADDRESS",
                "CITY",
                "DATE_OF_BIRTH",
                "NATIONAL_ID",
                "PASSPORT_NUMBER",
                "TAX_ID",
                "BIOMETRIC_ID",
                "HEALTH_INSURANCE_ID",
                "DIAGNOSIS",
                "MEDICATION",
                "CLINICAL_NOTE",
                "COOKIE_ID",
                "DEVICE_ID",
                "LOCATION",
                "COORDINATES",
                "FINANCIAL_ACCOUNT",
                "CREDIT_CARD_NUMBER",
                "BANK_ACCOUNT_NUMBER",
                "POLITICAL_OPINION",
                "RELIGION",
                "ETHNICITY",
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
                "CITY",
                "MEDICAL_RECORD_NUMBER",
                "HEALTH_INSURANCE_ID",
                "DIAGNOSIS",
                "MEDICATION",
                "CLINICAL_NOTE",
                "BIOMETRIC_ID",
                "SOCIAL_SECURITY_NUMBER",
                "IP_ADDRESS",
                "DEVICE_ID",
                "LICENSE_PLATE",
                "URL",
            ],
            is_builtin=True,
            is_active=is_active("hipaa"),
            custom_notes=None,
        ),
        RegulationRuleset(
            id="kvkk",
            display_name="Personal Data Protection Law (Turkey)",
            region="Turkey",
            description="6698 sayılı KVKK. Madde 3(d): kişisel veri tanımı. Madde 6: özel nitelikli kişisel veriler (ırk, etnik köken, siyasi düşünce, din, sağlık, cinsel hayat, biyometrik, genetik vb.). Kurum rehberi: ad, soyad, ana/baba adı, adres, doğum tarihi, telefon, plaka, SGK, pasaport.",
            official_url="https://www.kvkk.gov.tr/",
            entity_types=[
                "PERSON_NAME",
                "FIRST_NAME",
                "LAST_NAME",
                "NATIONAL_ID",
                "PASSPORT_NUMBER",
                "SOCIAL_SECURITY_NUMBER",
                "TAX_ID",
                "PHONE_NUMBER",
                "EMAIL_ADDRESS",
                "POSTAL_ADDRESS",
                "STREET_ADDRESS",
                "CITY",
                "LOCATION",
                "COORDINATES",
                "DATE_OF_BIRTH",
                "LICENSE_PLATE",
                "IP_ADDRESS",
                "COOKIE_ID",
                "DEVICE_ID",
                "BIOMETRIC_ID",
                "DNA_PROFILE",
                "HEALTH_INSURANCE_ID",
                "DIAGNOSIS",
                "MEDICATION",
                "CLINICAL_NOTE",
                "RELIGION",
                "ETHNICITY",
                "POLITICAL_OPINION",
                "SEXUAL_ORIENTATION",
                "FINANCIAL_ACCOUNT",
                "CREDIT_CARD_NUMBER",
                "BANK_ACCOUNT_NUMBER",
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
                "NATIONAL_ID",
                "EMAIL_ADDRESS",
                "PHONE_NUMBER",
                "POSTAL_ADDRESS",
                "DATE_OF_BIRTH",
                "LOCATION",
                "IP_ADDRESS",
                "COOKIE_ID",
                "CREDIT_CARD_NUMBER",
                "BANK_ACCOUNT_NUMBER",
                "HEALTH_INSURANCE_ID",
                "DIAGNOSIS",
                "BIOMETRIC_ID",
                "POLITICAL_OPINION",
                "RELIGION",
                "ETHNICITY",
                "SEXUAL_ORIENTATION",
            ],
            is_builtin=True,
            is_active=is_active("lgpd"),
            custom_notes=None,
        ),
        RegulationRuleset(
            id="ccpa",
            display_name="California Consumer Privacy Act",
            region="USA (California)",
            description="Cal. Civ. Code § 1798.140. Identifiers, customer records, protected classifications, biometric, geolocation, sensitive PI.",
            official_url="https://oag.ca.gov/privacy/ccpa",
            entity_types=[
                "PERSON_NAME",
                "FIRST_NAME",
                "LAST_NAME",
                "EMAIL_ADDRESS",
                "PHONE_NUMBER",
                "POSTAL_ADDRESS",
                "STREET_ADDRESS",
                "CITY",
                "IP_ADDRESS",
                "NATIONAL_ID",
                "SOCIAL_SECURITY_NUMBER",
                "PASSPORT_NUMBER",
                "DRIVERS_LICENSE",
                "DATE_OF_BIRTH",
                "BIOMETRIC_ID",
                "FINANCIAL_ACCOUNT",
                "CREDIT_CARD_NUMBER",
                "BANK_ACCOUNT_NUMBER",
                "DEVICE_ID",
                "COOKIE_ID",
                "COORDINATES",
                "LOCATION",
                "RELIGION",
                "ETHNICITY",
                "SEXUAL_ORIENTATION",
            ],
            is_builtin=True,
            is_active=is_active("ccpa"),
            custom_notes=None,
        ),
        RegulationRuleset(
            id="cpra",
            display_name="California Privacy Rights Act",
            region="USA (California)",
            description="CPRA amends CCPA; Cal. Civ. Code § 1798.140. Same categories plus sensitive personal information (precise geolocation, genetic, health).",
            official_url="https://oag.ca.gov/privacy/cpra",
            entity_types=[
                "PERSON_NAME",
                "FIRST_NAME",
                "LAST_NAME",
                "EMAIL_ADDRESS",
                "PHONE_NUMBER",
                "POSTAL_ADDRESS",
                "STREET_ADDRESS",
                "CITY",
                "IP_ADDRESS",
                "NATIONAL_ID",
                "SOCIAL_SECURITY_NUMBER",
                "PASSPORT_NUMBER",
                "DRIVERS_LICENSE",
                "DATE_OF_BIRTH",
                "BIOMETRIC_ID",
                "FINANCIAL_ACCOUNT",
                "CREDIT_CARD_NUMBER",
                "BANK_ACCOUNT_NUMBER",
                "DEVICE_ID",
                "COOKIE_ID",
                "COORDINATES",
                "LOCATION",
                "RELIGION",
                "ETHNICITY",
                "SEXUAL_ORIENTATION",
                "HEALTH_INSURANCE_ID",
                "DIAGNOSIS",
                "MEDICATION",
            ],
            is_builtin=True,
            is_active=is_active("cpra"),
            custom_notes=None,
        ),
        RegulationRuleset(
            id="uk_gdpr",
            display_name="UK GDPR",
            region="United Kingdom",
            description="UK GDPR (retained EU law) and DPA 2018. Same personal data definition as EU GDPR Art. 4(1); special categories Art. 9(1); ICO guidance on identifiers.",
            official_url="https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/",
            entity_types=[
                "PERSON_NAME",
                "FIRST_NAME",
                "LAST_NAME",
                "EMAIL_ADDRESS",
                "PHONE_NUMBER",
                "IP_ADDRESS",
                "MAC_ADDRESS",
                "POSTAL_ADDRESS",
                "STREET_ADDRESS",
                "CITY",
                "DATE_OF_BIRTH",
                "NATIONAL_ID",
                "PASSPORT_NUMBER",
                "TAX_ID",
                "BIOMETRIC_ID",
                "HEALTH_INSURANCE_ID",
                "DIAGNOSIS",
                "MEDICATION",
                "CLINICAL_NOTE",
                "COOKIE_ID",
                "DEVICE_ID",
                "LOCATION",
                "COORDINATES",
                "FINANCIAL_ACCOUNT",
                "CREDIT_CARD_NUMBER",
                "BANK_ACCOUNT_NUMBER",
                "POLITICAL_OPINION",
                "RELIGION",
                "ETHNICITY",
                "SEXUAL_ORIENTATION",
            ],
            is_builtin=True,
            is_active=is_active("uk_gdpr"),
            custom_notes=None,
        ),
        RegulationRuleset(
            id="pipeda",
            display_name="Personal Information Protection and Electronic Documents Act",
            region="Canada",
            description="PIPEDA s. 2(1): information about an identifiable individual. OPC guidance: financial, biometric, health, identifiers, opinions.",
            official_url="https://laws-lois.justice.gc.ca/eng/acts/P-8.6/",
            entity_types=[
                "PERSON_NAME",
                "FIRST_NAME",
                "LAST_NAME",
                "EMAIL_ADDRESS",
                "PHONE_NUMBER",
                "IP_ADDRESS",
                "POSTAL_ADDRESS",
                "STREET_ADDRESS",
                "CITY",
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
                "RELIGION",
                "ETHNICITY",
            ],
            is_builtin=True,
            is_active=is_active("pipeda"),
            custom_notes=None,
        ),
        RegulationRuleset(
            id="pdpa_th",
            display_name="Personal Data Protection Act",
            region="Thailand",
            description="Thailand PDPA (B.E. 2562).",
            official_url="https://www.pdpathailand.com/",
            entity_types=[
                "PERSON_NAME",
                "EMAIL_ADDRESS",
                "PHONE_NUMBER",
                "IP_ADDRESS",
                "POSTAL_ADDRESS",
                "DATE_OF_BIRTH",
                "NATIONAL_ID",
                "PASSPORT_NUMBER",
                "BIOMETRIC_ID",
                "HEALTH_INSURANCE_ID",
                "DIAGNOSIS",
                "COOKIE_ID",
                "DEVICE_ID",
                "LOCATION",
                "FINANCIAL_ACCOUNT",
                "CREDIT_CARD_NUMBER",
                "BANK_ACCOUNT_NUMBER",
                "RELIGION",
                "ETHNICITY",
                "POLITICAL_OPINION",
            ],
            is_builtin=True,
            is_active=is_active("pdpa_th"),
            custom_notes=None,
        ),
        RegulationRuleset(
            id="pdpa_sg",
            display_name="Personal Data Protection Act",
            region="Singapore",
            description="Singapore PDPA.",
            official_url="https://www.pdpc.gov.sg/",
            entity_types=[
                "PERSON_NAME",
                "EMAIL_ADDRESS",
                "PHONE_NUMBER",
                "IP_ADDRESS",
                "POSTAL_ADDRESS",
                "DATE_OF_BIRTH",
                "NATIONAL_ID",
                "PASSPORT_NUMBER",
                "BIOMETRIC_ID",
                "COOKIE_ID",
                "DEVICE_ID",
                "LOCATION",
                "FINANCIAL_ACCOUNT",
                "CREDIT_CARD_NUMBER",
                "BANK_ACCOUNT_NUMBER",
            ],
            is_builtin=True,
            is_active=is_active("pdpa_sg"),
            custom_notes=None,
        ),
        RegulationRuleset(
            id="appi",
            display_name="Act on the Protection of Personal Information",
            region="Japan",
            description="Japan APPI (Personal Information Protection Law).",
            official_url="https://www.ppc.go.jp/en/",
            entity_types=[
                "PERSON_NAME",
                "EMAIL_ADDRESS",
                "PHONE_NUMBER",
                "IP_ADDRESS",
                "POSTAL_ADDRESS",
                "DATE_OF_BIRTH",
                "NATIONAL_ID",
                "PASSPORT_NUMBER",
                "BIOMETRIC_ID",
                "HEALTH_INSURANCE_ID",
                "DIAGNOSIS",
                "COOKIE_ID",
                "DEVICE_ID",
                "LOCATION",
                "FINANCIAL_ACCOUNT",
                "CREDIT_CARD_NUMBER",
                "BANK_ACCOUNT_NUMBER",
            ],
            is_builtin=True,
            is_active=is_active("appi"),
            custom_notes=None,
        ),
        RegulationRuleset(
            id="pipl",
            display_name="Personal Information Protection Law",
            region="China",
            description="China PIPL (Personal Information Protection Law).",
            official_url="https://www.cac.gov.cn/",
            entity_types=[
                "PERSON_NAME",
                "EMAIL_ADDRESS",
                "PHONE_NUMBER",
                "IP_ADDRESS",
                "POSTAL_ADDRESS",
                "DATE_OF_BIRTH",
                "NATIONAL_ID",
                "PASSPORT_NUMBER",
                "BIOMETRIC_ID",
                "COOKIE_ID",
                "DEVICE_ID",
                "LOCATION",
                "FINANCIAL_ACCOUNT",
                "CREDIT_CARD_NUMBER",
                "BANK_ACCOUNT_NUMBER",
                "RELIGION",
                "ETHNICITY",
            ],
            is_builtin=True,
            is_active=is_active("pipl"),
            custom_notes=None,
        ),
        RegulationRuleset(
            id="popia",
            display_name="Protection of Personal Information Act",
            region="South Africa",
            description="South Africa POPIA (Act 4 of 2013).",
            official_url="https://popia.co.za/",
            entity_types=[
                "PERSON_NAME",
                "EMAIL_ADDRESS",
                "PHONE_NUMBER",
                "IP_ADDRESS",
                "POSTAL_ADDRESS",
                "DATE_OF_BIRTH",
                "NATIONAL_ID",
                "PASSPORT_NUMBER",
                "BIOMETRIC_ID",
                "HEALTH_INSURANCE_ID",
                "DIAGNOSIS",
                "COOKIE_ID",
                "DEVICE_ID",
                "LOCATION",
                "FINANCIAL_ACCOUNT",
                "CREDIT_CARD_NUMBER",
                "BANK_ACCOUNT_NUMBER",
                "RELIGION",
                "ETHNICITY",
                "POLITICAL_OPINION",
            ],
            is_builtin=True,
            is_active=is_active("popia"),
            custom_notes=None,
        ),
        RegulationRuleset(
            id="dpdp",
            display_name="Digital Personal Data Protection Act",
            region="India",
            description="India DPDP Act 2023.",
            official_url="https://www.meity.gov.in/data-protection-framework",
            entity_types=[
                "PERSON_NAME",
                "EMAIL_ADDRESS",
                "PHONE_NUMBER",
                "IP_ADDRESS",
                "POSTAL_ADDRESS",
                "DATE_OF_BIRTH",
                "NATIONAL_ID",
                "PASSPORT_NUMBER",
                "BIOMETRIC_ID",
                "HEALTH_INSURANCE_ID",
                "DIAGNOSIS",
                "COOKIE_ID",
                "DEVICE_ID",
                "LOCATION",
                "FINANCIAL_ACCOUNT",
                "CREDIT_CARD_NUMBER",
                "BANK_ACCOUNT_NUMBER",
            ],
            is_builtin=True,
            is_active=is_active("dpdp"),
            custom_notes=None,
        ),
        RegulationRuleset(
            id="pdpl_sa",
            display_name="Personal Data Protection Law",
            region="Saudi Arabia",
            description="Saudi Arabia PDPL (Royal Decree M/19).",
            official_url="https://sdaia.gov.sa/",
            entity_types=[
                "PERSON_NAME",
                "EMAIL_ADDRESS",
                "PHONE_NUMBER",
                "IP_ADDRESS",
                "POSTAL_ADDRESS",
                "DATE_OF_BIRTH",
                "NATIONAL_ID",
                "PASSPORT_NUMBER",
                "BIOMETRIC_ID",
                "COOKIE_ID",
                "DEVICE_ID",
                "LOCATION",
                "FINANCIAL_ACCOUNT",
                "CREDIT_CARD_NUMBER",
                "BANK_ACCOUNT_NUMBER",
            ],
            is_builtin=True,
            is_active=is_active("pdpl_sa"),
            custom_notes=None,
        ),
        RegulationRuleset(
            id="nzpa",
            display_name="Privacy Act 2020",
            region="New Zealand",
            description="New Zealand Privacy Act 2020.",
            official_url="https://www.privacy.org.nz/",
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
                "COOKIE_ID",
                "DEVICE_ID",
                "LOCATION",
                "FINANCIAL_ACCOUNT",
                "CREDIT_CARD_NUMBER",
                "BANK_ACCOUNT_NUMBER",
            ],
            is_builtin=True,
            is_active=is_active("nzpa"),
            custom_notes=None,
        ),
        RegulationRuleset(
            id="australia_pa",
            display_name="Privacy Act 1988",
            region="Australia",
            description="Australia Privacy Act 1988 (amended).",
            official_url="https://www.oaic.gov.au/privacy/",
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
                "COOKIE_ID",
                "DEVICE_ID",
                "LOCATION",
                "FINANCIAL_ACCOUNT",
                "CREDIT_CARD_NUMBER",
                "BANK_ACCOUNT_NUMBER",
            ],
            is_builtin=True,
            is_active=is_active("australia_pa"),
            custom_notes=None,
        ),
    ]

