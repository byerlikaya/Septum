from __future__ import annotations

"""
Application configuration helpers for Septum.

This module provides a lightweight, synchronous `get_settings` function which
constructs an `AppSettings` instance from environment variables. It is intended
for simple scripts and local utilities which need access to the same defaults
used by the main application, without going through the database layer.
"""

import os
from typing import List

from .models.settings import AppSettings


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


def get_settings() -> AppSettings:
    """Return an `AppSettings` instance built from environment defaults.

    This mirrors the defaults used in the database seeding logic but does not
    require a database connection, making it suitable for quick CLI scripts
    and ad-hoc tests.
    """
    default_active_regs_env = os.getenv("DEFAULT_ACTIVE_REGULATIONS", "gdpr").strip()
    default_active_regulations: List[str] = [
        r.strip().lower() for r in default_active_regs_env.split(",") if r.strip()
    ] or ["gdpr"]

    return AppSettings(
        id=1,
        llm_provider=os.getenv("LLM_PROVIDER", "anthropic"),
        llm_model=os.getenv("LLM_MODEL", "claude-3-5-sonnet-latest"),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        ollama_chat_model=os.getenv("OLLAMA_CHAT_MODEL", "llama3.2:3b"),
        ollama_deanon_model=os.getenv("OLLAMA_DEANON_MODEL", "llama3.2:3b"),
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
        spreadsheet_chunk_size=_env_int("SPREADSHEET_CHUNK_SIZE_DEFAULT", 200),
        whisper_model=os.getenv("WHISPER_MODEL", "base"),
        image_ocr_languages=_csv_env_to_list("DEFAULT_OCR_LANGUAGES", default="en,tr,de,ru,fr"),
        extract_embedded_images=_env_bool("EXTRACT_EMBEDDED_IMAGES_DEFAULT", True),
        recursive_email_attachments=_env_bool(
            "RECURSIVE_EMAIL_ATTACHMENTS_DEFAULT", True
        ),
        default_active_regulations=default_active_regulations,
    )

