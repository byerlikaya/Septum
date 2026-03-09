from __future__ import annotations

from typing import Any, Dict

import pytest

from backend.app.models.settings import AppSettings
from backend.app.services.anonymization_map import AnonymizationMap
from backend.app.services.deanonymizer import Deanonymizer


class _DummyOllamaClient:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload
        self.last_url: str | None = None
        self.last_json: Dict[str, Any] | None = None

    async def __aenter__(self) -> "_DummyOllamaClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        return None

    async def post(self, url: str, json: Dict[str, Any] | None = None) -> "_DummyOllamaResponse":
        self.last_url = url
        self.last_json = json or {}
        return _DummyOllamaResponse(self._payload)


class _DummyOllamaResponse:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> Dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        return None


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
    """Ollama strategy should call the local Ollama HTTP API."""
    from backend.app.services import deanonymizer as deanonymizer_module

    payload = {"response": "Refined answer with Alice."}
    dummy_client = _DummyOllamaClient(payload)

    def _client_factory(*args: Any, **kwargs: Any) -> _DummyOllamaClient:
        return dummy_client

    monkeypatch.setattr(deanonymizer_module.httpx, "AsyncClient", _client_factory)

    settings = _base_settings(strategy="ollama", enabled=True)
    dean = Deanonymizer(settings=settings)

    amap = AnonymizationMap(document_id=2, language="en")
    amap.add_entity("Alice", "PERSON_NAME")

    text = "Hello [PERSON_NAME_1]"
    result = await dean.deanonymize(text, anon_map=amap)

    assert result == "Refined answer with Alice."
    assert dummy_client.last_url is not None
    assert dummy_client.last_url.endswith("/api/generate")
    assert dummy_client.last_json is not None
    assert dummy_client.last_json["model"] == settings.ollama_deanon_model

