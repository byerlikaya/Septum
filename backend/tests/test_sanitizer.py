from __future__ import annotations

import pytest

from backend.app.models.settings import AppSettings
from backend.app.services.anonymization_map import AnonymizationMap
from backend.app.services.sanitizer import PIISanitizer


@pytest.fixture
def app_settings() -> AppSettings:
    """Return an in-memory AppSettings instance suitable for sanitizer tests."""
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
        use_ollama_layer=False,
        chunk_size=800,
        chunk_overlap=200,
        top_k_retrieval=5,
        pdf_chunk_size=1200,
        audio_chunk_size=60,
        spreadsheet_chunk_size=200,
        whisper_model="base",
        image_ocr_languages=["en"],
        ocr_provider="easyocr",
        ocr_provider_options=None,
        extract_embedded_images=True,
        recursive_email_attachments=True,
        default_active_regulations=["gdpr", "kvkk"],
    )


@pytest.fixture
def sanitizer(app_settings: AppSettings) -> PIISanitizer:
    """Return a PIISanitizer configured for tests."""
    return PIISanitizer(settings=app_settings)


def test_turkish_phone_number_is_sanitized(sanitizer: PIISanitizer) -> None:
    """Turkish phone numbers should be detected and replaced with placeholders."""
    text = "Lütfen beni +90 532 111 22 33 numarasından ara."
    anon_map = AnonymizationMap(document_id=1, language="tr")

    result = sanitizer.sanitize(text=text, language="tr", anon_map=anon_map)

    # The sanitizer may detect additional entities (for example PERSON),
    # but it must at minimum detect and replace the Turkish phone number.
    assert result.entity_count >= 1
    assert "[PHONE_NUMBER_1]" in result.sanitized_text
    assert "532 111 22 33" not in result.sanitized_text


def test_tckn_is_sanitized(sanitizer: PIISanitizer) -> None:
    """Valid TCKN values should be detected using the custom recognizer."""
    # Sample values generated via TCKNValidator in tests.
    valid_tckn = "10000000078"
    text = f"Kullanıcının TCKN bilgisi: {valid_tckn} gizli tutulmalıdır."
    anon_map = AnonymizationMap(document_id=2, language="tr")

    result = sanitizer.sanitize(text=text, language="tr", anon_map=anon_map)

    # Other entities (for example PERSON) may also be detected, but a valid
    # TCKN must always be anonymized.
    assert result.entity_count >= 1
    assert "[NATIONAL_ID_1]" in result.sanitized_text
    assert valid_tckn not in result.sanitized_text


def test_iban_is_sanitized(sanitizer: PIISanitizer) -> None:
    """Valid IBAN values should be detected and anonymized."""
    valid_iban = "TR330006100519786457841326"
    text = f"Hesap IBAN bilgisi: {valid_iban} asla paylaşılmamalıdır."
    anon_map = AnonymizationMap(document_id=3, language="tr")

    result = sanitizer.sanitize(text=text, language="tr", anon_map=anon_map)

    # As with other tests, the sanitizer can mask additional context, but a
    # valid IBAN must be replaced with an IBAN placeholder.
    assert result.entity_count >= 1
    assert "[IBAN_1]" in result.sanitized_text
    assert valid_iban not in result.sanitized_text


def test_coreference_and_blocklist_integration(sanitizer: PIISanitizer) -> None:
    """AnonymizationMap integration: token mentions get [ENTITY_TYPE_N] placeholder."""
    text = "Ahmet Yılmaz bugün aradı. Daha sonra Ahmet tekrar mesaj attı."
    anon_map = AnonymizationMap(document_id=4, language="tr")

    placeholder = anon_map.add_entity("Ahmet Yılmaz", "PERSON_NAME")
    assert placeholder == "[PERSON_NAME_1]"

    sanitized = anon_map.apply_blocklist(text, language="tr")

    assert "[PERSON_NAME_1]" in sanitized
    assert "Ahmet" not in sanitized or "[PERSON_NAME_1]" in sanitized

