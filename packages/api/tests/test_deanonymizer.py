from __future__ import annotations

import pytest

from septum_api.models.settings import AppSettings
from septum_api.services.anonymization_map import AnonymizationMap
from septum_api.services.deanonymizer import Deanonymizer, DeAnonymizer


def _base_settings(strategy: str, enabled: bool = True) -> AppSettings:
    return AppSettings(
        id=1,
        llm_provider="anthropic",
        llm_model="claude-3-5-sonnet-latest",
        ollama_base_url="http://localhost:11434",
        ollama_chat_model="llama3.2:3b",
        ollama_deanon_model="llama3.2:3b",
        deanon_enabled=enabled,
        deanon_strategy=strategy,
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
        ocr_provider="paddleocr",
        ocr_provider_options=None,
        extract_embedded_images=True,
        recursive_email_attachments=True,
        default_active_regulations=["gdpr"],
    )


@pytest.mark.asyncio
async def test_deanonymizer_simple_strategy_replaces_placeholders() -> None:
    """Simple strategy must replace known placeholders with originals."""
    settings = _base_settings(strategy="simple", enabled=True)
    deanonymizer = Deanonymizer(settings=settings)

    amap = AnonymizationMap(document_id=1, language="en")
    # Populate the map as the sanitizer would.
    placeholder = amap.add_entity("Alice", "PERSON_NAME")
    assert placeholder == "[PERSON_NAME_1]"

    text = "Hello [PERSON_NAME_1], welcome!"
    result = deanonymizer.deanonymize(text, anon_map=amap)

    assert result == "Hello Alice, welcome!"


@pytest.mark.asyncio
async def test_deanonymizer_simple_replaces_short_form_person_alias() -> None:
    """When the map has [PERSON_NAME_N], LLM may return [PERSON_N]; both must be deanon'd."""
    settings = _base_settings(strategy="simple", enabled=True)
    deanonymizer = Deanonymizer(settings=settings)

    amap = AnonymizationMap(document_id=1, language="en")
    amap.add_entity("Ahmet Yılmaz", "PERSON_NAME")
    amap.add_entity("Ayşe Kaya", "PERSON_NAME")
    # Map has [PERSON_NAME_1], [PERSON_NAME_2]; LLM often returns [PERSON_1], [PERSON_2].
    text = "- [PERSON_1]\n- [PERSON_2]"
    result = deanonymizer.deanonymize(text, anon_map=amap)

    assert result == "- Ahmet Yılmaz\n- Ayşe Kaya"


@pytest.mark.asyncio
async def test_deanonymizer_disabled_returns_original_text() -> None:
    """When de-anonymization is disabled, the input text must be returned."""
    settings = _base_settings(strategy="simple", enabled=False)
    deanonymizer = Deanonymizer(settings=settings)

    amap = AnonymizationMap(document_id=1, language="en")
    amap.add_entity("Alice", "PERSON_NAME")

    text = "Hello [PERSON_NAME_1]"
    result = deanonymizer.deanonymize(text, anon_map=amap)

    assert result == text




@pytest.mark.asyncio
async def test_deanonymizer_round_trips_date_placeholders() -> None:
    """[DATE_1]/[DATE_2] in an LLM answer map back to the right originals."""
    deanonymizer = Deanonymizer(settings=_base_settings(strategy="simple"))
    amap = AnonymizationMap(document_id=1, language="en")
    assert amap.add_entity("2024-03-15", "DATE") == "[DATE_1]"
    assert amap.add_entity("2024-04-02", "DATE") == "[DATE_2]"

    text = "Admitted [DATE_1], follow-up on [DATE_2]; discharge matches [DATE_1]."
    result = deanonymizer.deanonymize(text, anon_map=amap)

    assert result == (
        "Admitted 2024-03-15, follow-up on 2024-04-02; discharge matches 2024-03-15."
    )


async def test_sync_wrapper_works_inside_a_running_event_loop() -> None:
    """The sync wrapper must not start its own loop: it is callable from async code."""
    result = DeAnonymizer().deanonymize(
        "Contact [PERSON_1] today.", {"[PERSON_1]": "Jane Example"}
    )

    assert result == "Contact Jane Example today."
