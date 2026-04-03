"""PII escape scenario tests — multilingual, LLM contamination, and edge cases.

All LLM calls are mocked. These tests verify that PII does not leak through
sanitization across various scenarios.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.app.models.settings import AppSettings
from backend.app.services.anonymization_map import AnonymizationMap
from backend.app.services.sanitizer import PIISanitizer


@pytest.fixture
def app_settings() -> AppSettings:
    return AppSettings(
        id=1,
        llm_provider="anthropic",
        llm_model="claude-3-5-sonnet-latest",
        ollama_base_url="http://localhost:11434",
        ollama_chat_model="llama3.2:3b",
        ollama_deanon_model="llama3.2:3b",
        deanon_enabled=True,
        deanon_strategy="simple",
        require_approval=False,
        show_json_output=False,
        use_presidio_layer=True,
        use_ner_layer=False,
        use_ollama_validation_layer=False,
        use_ollama_layer=False,
        chunk_size=800,
        chunk_overlap=200,
        top_k_retrieval=5,
        pdf_chunk_size=1200,
        audio_chunk_size=60,
        spreadsheet_chunk_size=200,
        whisper_model="base",
        image_ocr_languages=["en"],
        ocr_provider="paddleocr",
        ocr_provider_options=None,
        extract_embedded_images=True,
        recursive_email_attachments=True,
        default_active_regulations=["gdpr"],
    )


@pytest.fixture
def sanitizer(app_settings: AppSettings) -> PIISanitizer:
    return PIISanitizer(settings=app_settings)


# --- Multilingual PII escape scenarios ---


def test_case_variation_maps_to_same_placeholder() -> None:
    """Case variations of same name should resolve to the same placeholder."""
    amap = AnonymizationMap(document_id=1, language="en")

    ph1 = amap.add_entity("John Smith", "PERSON_NAME")
    ph2 = amap.add_entity("JOHN SMITH", "PERSON_NAME")
    ph3 = amap.add_entity("john smith", "PERSON_NAME")

    assert ph1 == ph2 == ph3


def test_unicode_nfc_nfd_normalization() -> None:
    """NFC and NFD forms of the same name should resolve to the same entity."""
    import unicodedata

    amap = AnonymizationMap(document_id=1, language="de")

    nfc_name = unicodedata.normalize("NFC", "M\u00fcller")
    nfd_name = unicodedata.normalize("NFD", "M\u00fcller")

    ph_nfc = amap.add_entity(nfc_name, "PERSON_NAME")
    ph_nfd = amap.add_entity(nfd_name, "PERSON_NAME")

    assert ph_nfc == ph_nfd


def test_blocklist_catches_partial_name_in_later_text() -> None:
    """After seeing a full name, partial mentions in later text should be caught by blocklist."""
    amap = AnonymizationMap(document_id=1, language="en")
    amap.add_entity("Michael Johnson", "PERSON_NAME")

    text = "Then Michael arrived at the office."
    redacted = amap.apply_blocklist(text, language="en")

    assert "Michael" not in redacted
    assert "[PERSON_NAME_1]" in redacted


# --- LLM Response Contamination Tests ---


def test_literal_placeholder_in_document_not_confused() -> None:
    """Text that literally contains bracket notation should not be confused with real placeholders."""
    amap = AnonymizationMap(document_id=1, language="en")

    text_with_literal_brackets = 'The format is [PERSON_NAME_1] for anonymized names.'

    blocklisted = amap.apply_blocklist(text_with_literal_brackets, language="en")
    assert "[PERSON_NAME_1]" in blocklisted


def test_blocklist_preserves_existing_placeholders_unchanged() -> None:
    """apply_blocklist should not modify existing placeholder tokens."""
    amap = AnonymizationMap(document_id=1, language="en")
    amap.add_entity("John", "PERSON_NAME")

    text = "[PERSON_NAME_1] said hello to John."
    result = amap.apply_blocklist(text, language="en")

    assert "[PERSON_NAME_1]" in result
    assert "John" not in result


# --- SanitizeResult entity_type_counts tests ---


def test_sanitize_result_includes_entity_type_counts(sanitizer: PIISanitizer) -> None:
    """SanitizeResult should include per-type entity counts."""
    text = "Call +90 532 111 22 33 or email test@example.com"
    anon_map = AnonymizationMap(document_id=1, language="en")

    result = sanitizer.sanitize(text, "en", anon_map)

    assert isinstance(result.entity_type_counts, dict)
    assert result.entity_count >= 0
    total_from_counts = sum(result.entity_type_counts.values())
    assert total_from_counts == result.entity_count


def test_empty_text_returns_empty_entity_type_counts(sanitizer: PIISanitizer) -> None:
    """Empty text should return empty entity_type_counts."""
    anon_map = AnonymizationMap(document_id=1, language="en")
    result = sanitizer.sanitize("", "en", anon_map)

    assert result.entity_type_counts == {}
    assert result.entity_count == 0
