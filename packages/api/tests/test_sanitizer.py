from __future__ import annotations

from unittest.mock import patch

import pytest

from septum_api.models.settings import AppSettings
from septum_api.services.anonymization_map import AnonymizationMap
from septum_api.services.policy_composer import ComposedPolicy
from septum_core.recognizers.kvkk.recognizers import (
    get_recognizers as kvkk_get_recognizers,
)
from septum_api.services.sanitizer import (
    DetectedSpan,
    PIISanitizer,
    _PRESIDIO_ENTITY_ALIASES,
    _PRESIDIO_REVERSE_ALIASES,
)


def _kvkk_policy() -> ComposedPolicy:
    """Return an in-memory policy loaded with the KVKK recognizer pack.

    Baseline recognizers are regulation-agnostic; country-specific
    checksum validation (e.g. for 11-digit national IDs) lives inside
    regulation packs. Tests that exercise TCKN detection therefore need
    to activate the KVKK pack explicitly via this helper.

    The entity type list mirrors the superset of types exercised by
    this test file so that ``PIISanitizer`` does not filter detections
    when a policy is attached.
    """
    return ComposedPolicy(
        entity_types=[
            "NATIONAL_ID",
            "EMAIL_ADDRESS",
            "PHONE_NUMBER",
            "IBAN",
            "CREDIT_CARD_NUMBER",
            "PERSON_NAME",
            "LOCATION",
            "ORGANIZATION_NAME",
            "DATE_OF_BIRTH",
            "MAC_ADDRESS",
            "URL",
            "COORDINATES",
            "COOKIE_ID",
            "DEVICE_ID",
            "SOCIAL_SECURITY_NUMBER",
            "CPF",
            "PASSPORT_NUMBER",
            "DRIVERS_LICENSE",
            "TAX_ID",
            "LICENSE_PLATE",
            "ADDRESS",
        ],
        recognizers=list(kvkk_get_recognizers()),
        regulation_ids=["kvkk"],
        non_pii_rules=[],
    )


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
        default_active_regulations=["gdpr", "kvkk"],
    )


@pytest.fixture
def sanitizer(app_settings: AppSettings) -> PIISanitizer:
    """Return a PIISanitizer with the KVKK pack pre-loaded.

    Most of this file exercises TR-language fixtures and expects the
    KVKK recognizer pack (checksum-validated national IDs, Turkish
    phone formats, etc.) to be active. Keeping the fixture opinionated
    avoids each test having to re-declare the same policy.
    """
    return PIISanitizer(settings=app_settings, policy=_kvkk_policy())


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


@patch("septum_api.services.sanitizer.call_ollama_sync", return_value="[]")
def test_ollama_validation_never_drops_national_id_when_llm_returns_empty(
    _mock_ollama: object,
    app_settings: AppSettings,
) -> None:
    """Passthrough IDs stay; non-passthrough spans are kept if LLM returns []."""
    app_settings.use_ollama_validation_layer = True
    sanitizer = PIISanitizer(settings=app_settings)
    text = "10000000078 HelloWorld"
    candidates = [
        DetectedSpan(start=0, end=11, entity_type="NATIONAL_ID", score=0.95),
        DetectedSpan(start=12, end=22, entity_type="PERSON_NAME", score=0.85),
    ]
    out = sanitizer._ollama_validate_pii_candidates(text, candidates, "tr")
    types = {s.entity_type for s in out}
    assert "NATIONAL_ID" in types
    assert "PERSON_NAME" in types
    assert len(out) == 2


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


def test_credit_card_with_spaces_is_sanitized(sanitizer: PIISanitizer) -> None:
    """Credit card numbers with space separators must be anonymized."""
    text = "Kart numarası: 4543 4555 6365 8596 ile ödeme yapıldı."
    anon_map = AnonymizationMap(document_id=5, language="tr")

    result = sanitizer.sanitize(text=text, language="tr", anon_map=anon_map)

    assert result.entity_count >= 1
    assert "[CREDIT_CARD_NUMBER_1]" in result.sanitized_text
    assert "4543 4555 6365 8596" not in result.sanitized_text


def test_credit_card_with_dashes_is_sanitized(sanitizer: PIISanitizer) -> None:
    """Credit card numbers with dash separators must be anonymized."""
    text = "Card: 5425-2334-3010-9903 charged $50."
    anon_map = AnonymizationMap(document_id=6, language="en")

    result = sanitizer.sanitize(text=text, language="en", anon_map=anon_map)

    assert result.entity_count >= 1
    assert "[CREDIT_CARD_NUMBER_1]" in result.sanitized_text
    assert "5425-2334-3010-9903" not in result.sanitized_text


def test_credit_card_no_separator_is_sanitized(sanitizer: PIISanitizer) -> None:
    """Credit card numbers without separators must be anonymized."""
    text = "Payment from card 4111111111111111 confirmed."
    anon_map = AnonymizationMap(document_id=7, language="en")

    result = sanitizer.sanitize(text=text, language="en", anon_map=anon_map)

    assert result.entity_count >= 1
    assert "[CREDIT_CARD_NUMBER_1]" in result.sanitized_text
    assert "4111111111111111" not in result.sanitized_text


def test_credit_card_amex_is_sanitized(sanitizer: PIISanitizer) -> None:
    """American Express card numbers (15 digits, 3xxx prefix) must be anonymized."""
    text = "Amex card 3782 822463 10005 on file."
    anon_map = AnonymizationMap(document_id=8, language="en")

    result = sanitizer.sanitize(text=text, language="en", anon_map=anon_map)

    assert result.entity_count >= 1
    assert "[CREDIT_CARD_NUMBER_1]" in result.sanitized_text
    assert "3782 822463 10005" not in result.sanitized_text


@patch("septum_api.services.sanitizer.call_ollama_sync", return_value="[]")
def test_credit_card_bypasses_ollama_validation(
    _mock_ollama: object,
    app_settings: AppSettings,
) -> None:
    """CREDIT_CARD_NUMBER is a high-priority entity; Ollama must not drop it."""
    app_settings.use_ollama_validation_layer = True
    sanitizer = PIISanitizer(settings=app_settings)
    text = "4543 4555 6365 8596"
    candidates = [
        DetectedSpan(start=0, end=19, entity_type="CREDIT_CARD_NUMBER", score=0.85),
    ]
    out = sanitizer._ollama_validate_pii_candidates(text, candidates, "tr")
    assert len(out) == 1
    assert out[0].entity_type == "CREDIT_CARD_NUMBER"


def test_presidio_alias_mapping_is_bidirectional() -> None:
    """Every alias in _PRESIDIO_ENTITY_ALIASES must have a reverse entry."""
    for septum_type, presidio_type in _PRESIDIO_ENTITY_ALIASES.items():
        assert _PRESIDIO_REVERSE_ALIASES[presidio_type] == septum_type


def test_expand_with_aliases_adds_presidio_types(sanitizer: PIISanitizer) -> None:
    """_expand_with_aliases should add Presidio built-in names to the entity list."""
    entity_types = ["PERSON_NAME", "CREDIT_CARD_NUMBER", "PHONE_NUMBER"]
    expanded = sanitizer._expand_with_aliases(entity_types)
    assert "CREDIT_CARD" in expanded
    assert "CREDIT_CARD_NUMBER" in expanded
    assert "PERSON_NAME" in expanded


def test_expand_with_aliases_returns_none_for_none(sanitizer: PIISanitizer) -> None:
    """_expand_with_aliases should return None when input is None."""
    assert sanitizer._expand_with_aliases(None) is None


def test_valid_tckn_with_prefix_narrows_span_to_digits(
    sanitizer: PIISanitizer,
) -> None:
    """``Kimlik No: 10000000078`` must be detected with the keyword excluded."""
    valid_tckn = "10000000078"
    text = f"Kimlik No: {valid_tckn} başvuru formunda yer alıyor."
    anon_map = AnonymizationMap(document_id=9, language="tr")

    result = sanitizer.sanitize(text=text, language="tr", anon_map=anon_map)

    assert "[NATIONAL_ID_1]" in result.sanitized_text
    assert valid_tckn not in result.sanitized_text
    # The "Kimlik No:" prefix must survive — only the digits are the entity.
    assert "Kimlik No:" in result.sanitized_text


def test_invalid_tckn_with_prefix_is_still_masked_at_fallback_score(
    sanitizer: PIISanitizer,
) -> None:
    """Synthetic / mistyped 11-digit TCKNs still get a NATIONAL_ID placeholder.

    Earlier behaviour dropped every TCKN that failed the checksum
    validator, which silently leaked synthetic test data, typos, and
    OCR-mangled real IDs. The new ``fallback_score`` contract on
    ``ValidatedPatternRecognizer`` mirrors the policy already used by
    ``CreditCardNumberRecognizer`` (no Luhn check) and the
    format-only IBAN fallback: keep the match alive at a reduced
    score so privacy-first pipelines still mask the value. The
    ``Test verisi No:`` context keyword is enough to promote
    ``61504839271`` to a ``NATIONAL_ID`` placeholder even though the
    TCKN checksum rejects it.
    """
    invalid_tckn = "61504839271"
    text = f"Test verisi No: {invalid_tckn} sahte bir belgede geçmektedir."
    anon_map = AnonymizationMap(document_id=10, language="tr")

    result = sanitizer.sanitize(text=text, language="tr", anon_map=anon_map)

    assert "[NATIONAL_ID_" in result.sanitized_text
    assert invalid_tckn not in result.sanitized_text


def test_invalid_tc_dotted_prefix_is_masked(
    sanitizer: PIISanitizer,
) -> None:
    """``T.C. 29374810562`` must be masked as NATIONAL_ID even with a failing checksum.

    Reproduces a production leak where a KVKK başvuru formu PDF with
    a synthetic T.C. number passed through the ingestion pipeline
    unmasked because ``T.C.`` was not in the contextual-keyword
    alternation (dots are not word characters, so ``\\b``-anchored
    alternation cannot reach it). The dedicated Turkish-label
    recognizer closes that gap.
    """
    leaked_tckn = "29374810562"
    text = f"T.C. {leaked_tckn}\nİmza: _____________"
    anon_map = AnonymizationMap(document_id=11, language="tr")

    result = sanitizer.sanitize(text=text, language="tr", anon_map=anon_map)

    assert "[NATIONAL_ID_" in result.sanitized_text
    assert leaked_tckn not in result.sanitized_text


def test_invalid_vergi_no_across_newline_is_masked(
    sanitizer: PIISanitizer,
) -> None:
    """``Vergi No:\\n5312984760`` must be masked as TAX_ID.

    VKN (Vergi Kimlik Numarası) is a 10-digit Turkish tax identifier
    for legal entities. PDF extraction frequently drops the value
    onto the next line after the label; the recognizer must tolerate
    the newline between ``No:`` and the digits.
    """
    leaked_vkn = "5312984760"
    text = f"Vergi No:\n{leaked_vkn}\nAdres: Güllük Cad."
    anon_map = AnonymizationMap(document_id=12, language="tr")

    result = sanitizer.sanitize(text=text, language="tr", anon_map=anon_map)

    assert leaked_vkn not in result.sanitized_text
    assert ("[TAX_ID_" in result.sanitized_text) or (
        "[NATIONAL_ID_" in result.sanitized_text
    )


def test_tc_kimlik_no_with_colon_is_masked(
    sanitizer: PIISanitizer,
) -> None:
    """``T.C. Kimlik No: 12345678901`` must be masked as NATIONAL_ID."""
    leaked_tckn = "12345678901"
    text = f"T.C. Kimlik No: {leaked_tckn}"
    anon_map = AnonymizationMap(document_id=13, language="tr")

    result = sanitizer.sanitize(text=text, language="tr", anon_map=anon_map)

    assert "[NATIONAL_ID_" in result.sanitized_text
    assert leaked_tckn not in result.sanitized_text


def test_coverage_validation_logs_uncovered_types(
    app_settings: AppSettings, caplog: pytest.LogCaptureFixture
) -> None:
    """_validate_entity_coverage should warn about entity types with no recognizer."""
    from septum_api.services.policy_composer import ComposedPolicy

    policy = ComposedPolicy(
        entity_types=["PERSON_NAME", "FAKE_ENTITY_TYPE_XYZ"],
        recognizers=[],
        regulation_ids=["test"],
        non_pii_rules=[],
    )
    import logging

    with caplog.at_level(logging.WARNING, logger="septum_api.services.sanitizer"):
        PIISanitizer(settings=app_settings, policy=policy)

    assert any("FAKE_ENTITY_TYPE_XYZ" in msg for msg in caplog.messages)
    assert not any("PERSON_NAME" in msg for msg in caplog.messages)


def test_ner_loc_output_passes_conservative_filter(sanitizer: PIISanitizer) -> None:
    """NER LOC output runs through a multi-word-or-high-score gate.

    Stochastic multilingual NER models mis-tag common nouns and form-field
    labels as LOC at moderate confidence (Turkish "Doğum", "TARAFLAR";
    German form headers; similar across every language Septum supports).
    The detector mirrors its ORGANIZATION_NAME filter: a single-token LOC
    span survives only if the model's confidence is ≥ 0.95, and multi-word
    spans pass regardless. Real place names like "İstanbul" / "Berlin"
    routinely score 0.97+ on XLM-RoBERTa; common-noun mis-fires land in
    the 0.80–0.92 range and are dropped.
    """
    fake_ner_results = [
        # Common-noun false positives — below the 0.95 single-token gate
        {"entity_group": "LOC", "start": 0, "end": 5, "score": 0.88},    # "kabul"
        {"entity_group": "LOC", "start": 6, "end": 11, "score": 0.92},   # "Doğum"
        {"entity_group": "LOC", "start": 12, "end": 20, "score": 0.90},  # "TARAFLAR"
        # Real cities at high confidence — pass the gate
        {"entity_group": "LOC", "start": 21, "end": 29, "score": 0.98},  # "İstanbul"
        {"entity_group": "LOC", "start": 30, "end": 36, "score": 0.97},  # "Berlin"
    ]
    text = "kabul Doğum TARAFLAR İstanbul Berlin"
    spans = sanitizer._from_ner_results(fake_ner_results, text, "tr")
    assert len(spans) == 2
    assert {s.entity_type for s in spans} == {"LOCATION"}
    surviving = {text[s.start : s.end] for s in spans}
    assert surviving == {"İstanbul", "Berlin"}

    # Multi-word spans bypass the score gate even at modest confidence.
    multiword_results = [
        {"entity_group": "LOC", "start": 0, "end": 8, "score": 0.88},    # "New York"
    ]
    mw_spans = sanitizer._from_ner_results(multiword_results, "New York", "en")
    assert len(mw_spans) == 1
    assert mw_spans[0].entity_type == "LOCATION"

    # Sanity check: other NER labels still go through.
    person_results = [
        {"entity_group": "PER", "start": 0, "end": 5, "score": 0.95},
    ]
    person_spans = sanitizer._from_ner_results(person_results, "Ahmet", "tr")
    assert len(person_spans) == 1
    assert person_spans[0].entity_type == "PERSON_NAME"


# ── New recognizer tests ──


def test_date_of_birth_with_context(sanitizer: PIISanitizer) -> None:
    """Date of birth preceded by contextual keyword should be detected."""
    text = "Patient date of birth: 15/03/1990 registered today."
    anon_map = AnonymizationMap(document_id=100, language="en")
    result = sanitizer.sanitize(text=text, language="en", anon_map=anon_map)
    assert "15/03/1990" not in result.sanitized_text
    assert "[DATE_OF_BIRTH_1]" in result.sanitized_text


def test_date_of_birth_turkish_context(sanitizer: PIISanitizer) -> None:
    """Turkish birth date label should trigger detection."""
    text = "Doğum tarihi: 1985-07-22 olan hasta kaydı."
    anon_map = AnonymizationMap(document_id=101, language="tr")
    result = sanitizer.sanitize(text=text, language="tr", anon_map=anon_map)
    assert "1985-07-22" not in result.sanitized_text


def test_date_without_birth_context_not_dob(sanitizer: PIISanitizer) -> None:
    """A bare date without birth context should NOT be flagged as DATE_OF_BIRTH (may be DATE_TIME)."""
    text = "The report was published on 15/03/2024."
    anon_map = AnonymizationMap(document_id=102, language="en")
    result = sanitizer.sanitize(text=text, language="en", anon_map=anon_map)
    assert "[DATE_OF_BIRTH" not in result.sanitized_text


def test_mac_address_colon_format(sanitizer: PIISanitizer) -> None:
    """MAC addresses in colon notation should be detected."""
    text = "Device MAC: 00:1A:2B:3C:4D:5E connected."
    anon_map = AnonymizationMap(document_id=103, language="en")
    result = sanitizer.sanitize(text=text, language="en", anon_map=anon_map)
    assert "00:1A:2B:3C:4D:5E" not in result.sanitized_text


def test_mac_address_dash_format(sanitizer: PIISanitizer) -> None:
    """MAC addresses in dash notation should be detected."""
    text = "NIC: 00-1A-2B-3C-4D-5E registered."
    anon_map = AnonymizationMap(document_id=104, language="en")
    result = sanitizer.sanitize(text=text, language="en", anon_map=anon_map)
    assert "00-1A-2B-3C-4D-5E" not in result.sanitized_text


def test_url_detected(sanitizer: PIISanitizer) -> None:
    """HTTP/HTTPS URLs should be detected."""
    text = "Visit https://internal.company.com/patient/12345 for records."
    anon_map = AnonymizationMap(document_id=105, language="en")
    result = sanitizer.sanitize(text=text, language="en", anon_map=anon_map)
    assert "https://internal.company.com/patient/12345" not in result.sanitized_text


def test_coordinates_decimal(sanitizer: PIISanitizer) -> None:
    """Decimal degree coordinates should be detected."""
    text = "Location logged at 41.0082, 28.9784 during incident."
    anon_map = AnonymizationMap(document_id=106, language="en")
    result = sanitizer.sanitize(text=text, language="en", anon_map=anon_map)
    assert "41.0082" not in result.sanitized_text


def test_cookie_id_ga(sanitizer: PIISanitizer) -> None:
    """Google Analytics cookie IDs should be detected."""
    text = "Tracking cookie: _ga=GA1.2.1234567890.1234567890 was set."
    anon_map = AnonymizationMap(document_id=107, language="en")
    result = sanitizer.sanitize(text=text, language="en", anon_map=anon_map)
    assert "_ga=GA1.2.1234567890.1234567890" not in result.sanitized_text


def test_device_id_imei(sanitizer: PIISanitizer) -> None:
    """IMEI device identifiers should be detected."""
    text = "Phone registered with IMEI=353456789012345 on network."
    anon_map = AnonymizationMap(document_id=108, language="en")
    result = sanitizer.sanitize(text=text, language="en", anon_map=anon_map)
    assert "353456789012345" not in result.sanitized_text


def test_device_id_uuid(sanitizer: PIISanitizer) -> None:
    """UUID-based device IDs should be detected."""
    text = "device_id=550e8400-e29b-41d4-a716-446655440000 logged."
    anon_map = AnonymizationMap(document_id=109, language="en")
    result = sanitizer.sanitize(text=text, language="en", anon_map=anon_map)
    assert "550e8400-e29b-41d4-a716-446655440000" not in result.sanitized_text


def test_ssn_with_context(sanitizer: PIISanitizer) -> None:
    """US SSN with context keyword should be detected."""
    text = "Employee SSN: 078-05-1120 on file."
    anon_map = AnonymizationMap(document_id=110, language="en")
    result = sanitizer.sanitize(text=text, language="en", anon_map=anon_map)
    assert "078-05-1120" not in result.sanitized_text


def test_ssn_without_context_not_detected(sanitizer: PIISanitizer) -> None:
    """A 9-digit number without SSN context should not be flagged."""
    text = "Order number is 123-45-6789 for reference."
    anon_map = AnonymizationMap(document_id=111, language="en")
    result = sanitizer.sanitize(text=text, language="en", anon_map=anon_map)
    assert "[SOCIAL_SECURITY_NUMBER" not in result.sanitized_text


def test_cpf_valid(sanitizer: PIISanitizer) -> None:
    """Valid Brazilian CPF should be detected."""
    text = "CPF do cliente: 529.982.247-25 cadastrado."
    anon_map = AnonymizationMap(document_id=112, language="pt")
    result = sanitizer.sanitize(text=text, language="pt", anon_map=anon_map)
    assert "529.982.247-25" not in result.sanitized_text


def test_passport_multilingual_context(sanitizer: PIISanitizer) -> None:
    """Passport numbers should be detected with multilingual context keywords."""
    cases = [
        ("Passport number: U12345678 issued 2024.", "en"),
        ("Pasaport numarası: U12345678 düzenlendi.", "tr"),
        ("Reisepass Nummer: U12345678 ausgestellt.", "de"),
        ("Numéro de passeport: U12345678 délivré.", "fr"),
    ]
    for text, lang in cases:
        anon_map = AnonymizationMap(document_id=113, language=lang)
        result = sanitizer.sanitize(text=text, language=lang, anon_map=anon_map)
        assert "U12345678" not in result.sanitized_text, f"Failed for lang={lang}: {result.sanitized_text}"


def test_drivers_license_multilingual(sanitizer: PIISanitizer) -> None:
    """Driver's license numbers should be detected with multilingual context."""
    cases = [
        ("Driver's license: D12345678 on record.", "en"),
        ("Ehliyet no: D12345678 kayıtlı.", "tr"),
        ("Führerschein Nr: D12345678 registriert.", "de"),
    ]
    for text, lang in cases:
        anon_map = AnonymizationMap(document_id=114, language=lang)
        result = sanitizer.sanitize(text=text, language=lang, anon_map=anon_map)
        assert "D12345678" not in result.sanitized_text, f"Failed for lang={lang}: {result.sanitized_text}"


def test_tax_id_multilingual(sanitizer: PIISanitizer) -> None:
    """Tax IDs should be detected with multilingual context."""
    cases = [
        ("Tax ID: 12-3456789 on file.", "en"),
        ("Vergi numarası: 12-3456789 kayıtlı.", "tr"),
        ("Steuernummer: 12-3456789 registriert.", "de"),
    ]
    for text, lang in cases:
        anon_map = AnonymizationMap(document_id=115, language=lang)
        result = sanitizer.sanitize(text=text, language=lang, anon_map=anon_map)
        assert "12-3456789" not in result.sanitized_text, f"Failed for lang={lang}: {result.sanitized_text}"


def test_license_plate_multilingual(sanitizer: PIISanitizer) -> None:
    """License plates should be detected with multilingual context."""
    cases = [
        ("Vehicle plate number: 34 ABC 1234 registered.", "en"),
        ("Plaka: 34 ABC 1234 kayıtlı.", "tr"),
        ("Kennzeichen: B AB 1234 registriert.", "de"),
    ]
    for text, lang in cases:
        anon_map = AnonymizationMap(document_id=116, language=lang)
        result = sanitizer.sanitize(text=text, language=lang, anon_map=anon_map)
        assert "[LICENSE_PLATE" in result.sanitized_text, f"Failed for lang={lang}: {result.sanitized_text}"


# ── Phase 2a: structural recognizer improvements ──


def test_iban_space_grouped_valid(sanitizer: PIISanitizer) -> None:
    """Valid IBANs in paper/statement grouping must be detected."""
    text = "Hesap: DE89 3704 0044 0532 0130 00 aktif."
    anon_map = AnonymizationMap(document_id=200, language="de")
    result = sanitizer.sanitize(text=text, language="de", anon_map=anon_map)
    assert "DE89 3704 0044 0532 0130 00" not in result.sanitized_text
    assert "[IBAN_1]" in result.sanitized_text


def test_iban_format_only_fallback_for_synthetic(sanitizer: PIISanitizer) -> None:
    """Synthetic IBANs that fail MOD 97-10 still get the IBAN placeholder.

    Credit-card detection already prefers over-detection in a
    privacy-first pipeline. The IBAN fallback extends the same
    philosophy: a mis-typed IBAN is still bank-account information.
    """
    text = "IBAN: DE89 2004 1010 0540 1420 00 listed."
    anon_map = AnonymizationMap(document_id=201, language="de")
    result = sanitizer.sanitize(text=text, language="de", anon_map=anon_map)
    assert "DE89 2004 1010 0540 1420 00" not in result.sanitized_text
    assert "[IBAN_1]" in result.sanitized_text


def test_date_of_birth_month_name_multilingual(sanitizer: PIISanitizer) -> None:
    """DOB recognizer must catch ``day + month-name + year`` formats."""
    cases = [
        ("Geburtsdatum: 22. Juli 1991", "de"),
        ("Date de naissance: 09 février 1987", "fr"),
        ("Data de nascimento: 12 de março de 1983", "pt"),
        ("Fecha de nacimiento: 01 de octubre de 2024", "es"),
        ("DOB: August 15, 1980", "en"),
    ]
    for text, lang in cases:
        anon_map = AnonymizationMap(document_id=202, language=lang)
        result = sanitizer.sanitize(text=text, language=lang, anon_map=anon_map)
        assert "[DATE_OF_BIRTH" in result.sanitized_text, (
            f"DOB not detected for lang={lang}: {result.sanitized_text}"
        )


def test_tax_id_broadened_patterns(sanitizer: PIISanitizer) -> None:
    """TAX_ID pattern must cover slash / prefix / spaced / dotted variants."""
    cases = [
        ("Steuernummer: 27/431/07892", "27/431/07892", "de"),
        ("USt-IdNr.: DE298431076", "DE298431076", "de"),
        ("Steuer-ID: 77 523 164 890", "77 523 164 890", "de"),
        ("CIF: B-86432178", "B-86432178", "es"),
        ("N° TVA intracommunautaire: FR 47 834291076", "FR 47 834291076", "fr"),
        ("Inscrição Estadual: 123.456.789.112", "123.456.789.112", "pt"),
    ]
    for text, value, lang in cases:
        anon_map = AnonymizationMap(document_id=203, language=lang)
        result = sanitizer.sanitize(text=text, language=lang, anon_map=anon_map)
        assert value not in result.sanitized_text, (
            f"TAX_ID value not masked for {lang}: {result.sanitized_text}"
        )


# ── Phase 2b: GDPR and LGPD country-specific national IDs ──


def test_gdpr_pack_country_specific_national_ids(
    app_settings: AppSettings,
) -> None:
    """GDPR pack detectors must handle DE/ES/FR national-ID families.

    The fixture activates only the GDPR pack (no KVKK, no baseline
    NATIONAL_ID recognizer) so we verify the pack carries the
    detection responsibility end-to-end.
    """
    from septum_core.recognizers.gdpr.recognizers import (
        get_recognizers as gdpr_get_recognizers,
    )

    policy = ComposedPolicy(
        entity_types=[
            "NATIONAL_ID", "TAX_ID", "SOCIAL_SECURITY_NUMBER",
            "EMAIL_ADDRESS", "PHONE_NUMBER", "IP_ADDRESS",
        ],
        recognizers=list(gdpr_get_recognizers()),
        regulation_ids=["gdpr"],
        non_pii_rules=[],
    )
    sanitizer = PIISanitizer(settings=app_settings, policy=policy)

    cases = [
        ("Personalausweis-Nr.: T22000129", "NATIONAL_ID", "T22000129", "de"),
        ("Steuer-ID: 77 523 164 890", "TAX_ID", "77 523 164 890", "de"),
        ("Rentenversicherungs-Nr.: 50 220791 M 007", "NATIONAL_ID", "50 220791 M 007", "de"),
        ("DNI: 50.432.187-K", "NATIONAL_ID", "50.432.187-K", "es"),
        ("DNI: 28741095Z", "NATIONAL_ID", "28741095Z", "es"),
        ("NIE: X1234567L", "NATIONAL_ID", "X1234567L", "es"),
        ("N° Seguridad Social: 28 07 41095 33", "SOCIAL_SECURITY_NUMBER", "28 07 41095 33", "es"),
        ("NIR: 2 87 02 75 056 127 42", "SOCIAL_SECURITY_NUMBER", "2 87 02 75 056 127 42", "fr"),
        ("N° SIREN: 834 291 076", "TAX_ID", "834 291 076", "fr"),
    ]
    for text, _entity_type, value, lang in cases:
        anon_map = AnonymizationMap(document_id=204, language=lang)
        result = sanitizer.sanitize(text=text, language=lang, anon_map=anon_map)
        assert value not in result.sanitized_text, (
            f"{lang}: expected {value!r} masked, got {result.sanitized_text!r}"
        )


def test_phone_regex_rejects_bare_long_digit_sequences(
    sanitizer: PIISanitizer,
) -> None:
    """Bare ``11-13`` digit identifiers must not be caught as PHONE_NUMBER.

    Before tightening ExtendedPhoneRecognizer, the optional international
    prefix ``(?:\\+?\\d{1,3}\\s*)?`` greedily consumed 1-3 digits and
    turned Japanese My Number, Saudi Iqama and similar contiguous
    national identifiers into phone matches. The new pattern requires
    either an explicit ``+`` prefix or 3+ separator-delimited groups,
    so these strings are rejected by PHONE entirely.
    """
    cases = [
        "701040108923",   # Japan My Number
        "101048293",      # Saudi Iqama (9 digits contiguous)
        "7408125800082",  # South African ID (13 digits contiguous)
    ]
    for bare in cases:
        text = f"Reference: {bare} attached."
        anon_map = AnonymizationMap(document_id=300, language="en")
        result = sanitizer.sanitize(text=text, language="en", anon_map=anon_map)
        types = {sp.entity_type for sp in result.detected_spans}
        assert "PHONE_NUMBER" not in types, (
            f"Bare {bare!r} was mis-classified as PHONE_NUMBER: {types}"
        )


def test_national_id_wins_over_phone_on_equal_span(
    app_settings: AppSettings,
) -> None:
    """When PHONE and NATIONAL_ID cover the same span, NATIONAL_ID wins.

    A 12-digit My Number written as ``1234-5678-9012`` is a legitimate
    phone *shape* (four groups of digits separated by dashes) and a
    legitimate national ID shape (4-4-4 grouped). Dedup must prefer
    the more specific identifier type via the entity-type priority
    tiebreaker introduced alongside this test.
    """
    from septum_core.recognizers.appi.recognizers import (
        get_recognizers as appi_get_recognizers,
    )

    policy = ComposedPolicy(
        entity_types=["NATIONAL_ID", "PHONE_NUMBER", "EMAIL_ADDRESS"],
        recognizers=list(appi_get_recognizers()),
        regulation_ids=["appi"],
        non_pii_rules=[],
    )
    sanitizer = PIISanitizer(settings=app_settings, policy=policy)

    text = "My Number: 1234-5678-9012"
    anon_map = AnonymizationMap(document_id=301, language="en")
    result = sanitizer.sanitize(text=text, language="en", anon_map=anon_map)

    types = {sp.entity_type for sp in result.detected_spans}
    assert "NATIONAL_ID" in types, (
        f"Expected NATIONAL_ID in {types}, got {result.sanitized_text!r}"
    )
    assert "PHONE_NUMBER" not in types, (
        f"PHONE_NUMBER should have lost dedup: {types}"
    )


def test_lgpd_pack_civil_identity_document(
    app_settings: AppSettings,
) -> None:
    """LGPD pack detects RG (Registro Geral) via context preamble."""
    from septum_core.recognizers.lgpd.recognizers import (
        get_recognizers as lgpd_get_recognizers,
    )

    policy = ComposedPolicy(
        entity_types=["NATIONAL_ID", "TAX_ID", "EMAIL_ADDRESS"],
        recognizers=list(lgpd_get_recognizers()),
        regulation_ids=["lgpd"],
        non_pii_rules=[],
    )
    sanitizer = PIISanitizer(settings=app_settings, policy=policy)

    text = "RG: 45.238.917-4 SSP/SP"
    anon_map = AnonymizationMap(document_id=205, language="pt")
    result = sanitizer.sanitize(text=text, language="pt", anon_map=anon_map)
    assert "45.238.917-4" not in result.sanitized_text
    assert "[NATIONAL_ID_1]" in result.sanitized_text


