from __future__ import annotations

import os
from typing import Any, Dict, List

import pytest

from backend.app.models.settings import AppSettings
from backend.app.services.llm_router import LLMRouter


class _DummyResponse:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> Dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        return None


class _DummyAsyncClient:
    def __init__(self, response_payload: Dict[str, Any]) -> None:
        self._response_payload = response_payload
        self.last_url: str | None = None
        self.last_headers: Dict[str, Any] | None = None
        self.last_json: Dict[str, Any] | None = None

    async def __aenter__(self) -> "_DummyAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        return None

    async def post(
        self,
        url: str,
        headers: Dict[str, Any] | None = None,
        json: Dict[str, Any] | None = None,
    ) -> _DummyResponse:
        self.last_url = url
        self.last_headers = headers or {}
        self.last_json = json or {}
        return _DummyResponse(self._response_payload)


@pytest.fixture
def app_settings_anthropic() -> AppSettings:
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
        default_active_regulations=["gdpr"],
    )


@pytest.mark.asyncio
async def test_llm_router_anthropic_complete(monkeypatch: pytest.MonkeyPatch, app_settings_anthropic: AppSettings) -> None:
    """Anthropic provider should return concatenated text from content blocks."""
    from backend.app.services import llm_router as llm_router_module

    os.environ["ANTHROPIC_API_KEY"] = "test-key"

    payload = {
        "content": [
            {"type": "text", "text": "Hello "},
            {"type": "text", "text": "world!"},
        ]
    }

    dummy_client = _DummyAsyncClient(payload)

    def _client_factory(*args: Any, **kwargs: Any) -> _DummyAsyncClient:
        return dummy_client

    monkeypatch.setattr(llm_router_module.httpx, "AsyncClient", _client_factory)

    router = LLMRouter(settings=app_settings_anthropic)
    messages: List[Dict[str, str]] = [
        {"role": "user", "content": "Say hello."},
    ]

    result = await router.complete(messages=messages)

    assert result == "Hello world!"
    assert dummy_client.last_url == "https://api.anthropic.com/v1/messages"
    assert dummy_client.last_json is not None
    assert dummy_client.last_json["model"] == app_settings_anthropic.llm_model


@pytest.mark.asyncio
async def test_llm_router_openai_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenAI provider should stream the full message content in chunks."""
    from backend.app.services import llm_router as llm_router_module

    os.environ["OPENAI_API_KEY"] = "test-openai-key"

    payload = {
        "choices": [
            {"message": {"content": "Chunked response text."}},
        ]
    }

    dummy_client = _DummyAsyncClient(payload)

    def _client_factory(*args: Any, **kwargs: Any) -> _DummyAsyncClient:
        return dummy_client

    monkeypatch.setattr(llm_router_module.httpx, "AsyncClient", _client_factory)

    settings = AppSettings(
        id=1,
        llm_provider="openai",
        llm_model="gpt-4.1",
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
        chunk_size=8,
        chunk_overlap=0,
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

    router = LLMRouter(settings=settings)
    messages = [{"role": "user", "content": "Test."}]

    chunks: List[str] = []
    async for part in router.stream_chat(messages=messages):
        chunks.append(part)

    assert "".join(chunks) == "Chunked response text."
    assert dummy_client.last_url == "https://api.openai.com/v1/chat/completions"


@pytest.mark.asyncio
async def test_llm_router_openrouter(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenRouter provider should use the OpenRouter endpoint and payload."""
    from backend.app.services import llm_router as llm_router_module

    os.environ["OPENROUTER_API_KEY"] = "test-openrouter-key"

    payload = {
        "choices": [
            {"message": {"content": "Hello from OpenRouter!"}},
        ]
    }

    dummy_client = _DummyAsyncClient(payload)

    def _client_factory(*args: Any, **kwargs: Any) -> _DummyAsyncClient:
        return dummy_client

    monkeypatch.setattr(llm_router_module.httpx, "AsyncClient", _client_factory)

    settings = AppSettings(
        id=1,
        llm_provider="openrouter",
        llm_model="openrouter/model",
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
        default_active_regulations=["gdpr"],
    )

    router = LLMRouter(settings=settings)
    messages = [{"role": "user", "content": "Test OpenRouter."}]

    result = await router.complete(messages=messages)

    assert result == "Hello from OpenRouter!"
    assert dummy_client.last_url == "https://openrouter.ai/api/v1/chat/completions"
    assert dummy_client.last_json is not None
    assert dummy_client.last_json["model"] == settings.llm_model

