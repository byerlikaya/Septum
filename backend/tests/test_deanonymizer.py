from __future__ import annotations

import pytest

from backend.app.models.settings import AppSettings
from backend.app.services.anonymization_map import AnonymizationMap
from backend.app.services.deanonymizer import Deanonymizer


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
        ocr_provider="easyocr",
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
    result = await deanonymizer.deanonymize(text, anon_map=amap)

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
    result = await deanonymizer.deanonymize(text, anon_map=amap)

    assert result == "- Ahmet Yılmaz\n- Ayşe Kaya"


@pytest.mark.asyncio
async def test_deanonymizer_disabled_returns_original_text() -> None:
    """When de-anonymization is disabled, the input text must be returned."""
    settings = _base_settings(strategy="simple", enabled=False)
    deanonymizer = Deanonymizer(settings=settings)

    amap = AnonymizationMap(document_id=1, language="en")
    amap.add_entity("Alice", "PERSON_NAME")

    text = "Hello [PERSON_NAME_1]"
    result = await deanonymizer.deanonymize(text, anon_map=amap)

    assert result == text


@pytest.mark.asyncio
async def test_deanonymizer_ollama_strategy_uses_local_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ollama strategy should call the local Ollama HTTP API (via ollama_client)."""
    from backend.app.services import deanonymizer as deanonymizer_module

    async def _fake_call_ollama_async(
        prompt: str,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> str:
        _fake_call_ollama_async.last_prompt = prompt  # type: ignore[attr-defined]
        _fake_call_ollama_async.last_model = model  # type: ignore[attr-defined]
        return "Refined answer with Alice."

    monkeypatch.setattr(deanonymizer_module, "call_ollama_async", _fake_call_ollama_async)
    monkeypatch.setattr(deanonymizer_module, "use_ollama_enabled", lambda: True)

    settings = _base_settings(strategy="ollama", enabled=True)
    dean = Deanonymizer(settings=settings)

    amap = AnonymizationMap(document_id=2, language="en")
    amap.add_entity("Alice", "PERSON_NAME")

    text = "Hello [PERSON_NAME_1]"
    result = await dean.deanonymize(text, anon_map=amap)

    assert result == "Refined answer with Alice."
    assert getattr(_fake_call_ollama_async, "last_prompt", "")
    assert "[PERSON_NAME_1]" in getattr(_fake_call_ollama_async, "last_prompt", "")
    assert getattr(_fake_call_ollama_async, "last_model", None) == settings.ollama_deanon_model

