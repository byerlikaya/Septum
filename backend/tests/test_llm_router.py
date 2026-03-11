from __future__ import annotations

import os
from typing import Any, Dict, List

import httpx
import pytest

from backend.app.models.settings import AppSettings
from backend.app.services import ollama_client
from backend.app.services.llm_router import LLMRouter
from backend.tests.factories.app_settings_factory import make_app_settings


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


def _build_app_settings(provider: str, model: str) -> AppSettings:
    """Backward-compatible wrapper around the shared AppSettings factory."""
    return make_app_settings(provider=provider, model=model)


@pytest.fixture
def app_settings_anthropic() -> AppSettings:
    return _build_app_settings(
        provider="anthropic",
        model="claude-3-5-sonnet-latest",
    )


@pytest.mark.asyncio
async def test_llm_router_anthropic_complete(monkeypatch: pytest.MonkeyPatch, app_settings_anthropic: AppSettings) -> None:
    """Anthropic provider should return concatenated text from content blocks."""
    from backend.app.services.llm_providers import http_client as http_client_module

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

    monkeypatch.setattr(http_client_module, "httpx", type("M", (), {"AsyncClient": _client_factory}))

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
    from backend.app.services.llm_providers import http_client as http_client_module

    os.environ["OPENAI_API_KEY"] = "test-openai-key"

    payload = {
        "choices": [
            {"message": {"content": "Chunked response text."}},
        ]
    }

    dummy_client = _DummyAsyncClient(payload)

    def _client_factory(*args: Any, **kwargs: Any) -> _DummyAsyncClient:
        return dummy_client

    monkeypatch.setattr(http_client_module, "httpx", type("M", (), {"AsyncClient": _client_factory}))

    settings = _build_app_settings(
        provider="openai",
        model="gpt-4.1",
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
    from backend.app.services.llm_providers import http_client as http_client_module

    os.environ["OPENROUTER_API_KEY"] = "test-openrouter-key"

    payload = {
        "choices": [
            {"message": {"content": "Hello from OpenRouter!"}},
        ]
    }

    dummy_client = _DummyAsyncClient(payload)

    def _client_factory(*args: Any, **kwargs: Any) -> _DummyAsyncClient:
        return dummy_client

    monkeypatch.setattr(http_client_module, "httpx", type("M", (), {"AsyncClient": _client_factory}))

    settings = _build_app_settings(
        provider="openrouter",
        model="openrouter/model",
    )

    router = LLMRouter(settings=settings)
    messages = [{"role": "user", "content": "Test OpenRouter."}]

    result = await router.complete(messages=messages)

    assert result == "Hello from OpenRouter!"
    assert dummy_client.last_url == "https://openrouter.ai/api/v1/chat/completions"
    assert dummy_client.last_json is not None
    assert dummy_client.last_json["model"] == settings.llm_model


@pytest.mark.asyncio
async def test_llm_router_falls_back_to_ollama_on_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Router should call Ollama when cloud transport fails."""
    from backend.app.services.llm_providers import http_client as http_client_module

    # Configure environment so the provider path is taken, but force transport errors.
    os.environ["OPENAI_API_KEY"] = "test-openai-key"

    async def _failing_post(*args: Any, **kwargs: Any) -> Any:  # noqa: ARG001, ANN401
        raise httpx.HTTPError("network down")

    # Force all HTTP POSTs in the router module to fail, so that the router
    # transitions into the Ollama fallback path.
    class _AsyncClientWrapper:
        def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # type: ignore[no-untyped-def]
            return None

        async def post(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ARG002, ANN401
            return await _failing_post(*args, **kwargs)

    monkeypatch.setattr(http_client_module, "httpx", type("M", (), {"AsyncClient": _AsyncClientWrapper}))

    recorded_prompts: list[str] = []

    async def _fake_call_ollama_async(
        prompt: str,
        base_url: str | None = None,  # noqa: ARG001
        model: str | None = None,  # noqa: ARG001
        timeout: float = 30.0,  # noqa: ARG001
    ) -> str:
        recorded_prompts.append(prompt)
        return "Local Ollama fallback answer."

    monkeypatch.setattr(ollama_client, "call_ollama_async", _fake_call_ollama_async)

    settings = make_app_settings(
        provider="openai",
        model="gpt-4.1",
    )
    router = LLMRouter(settings=settings)
    messages: List[Dict[str, str]] = [
        {"role": "user", "content": "Explain fallback behaviour."},
    ]

    chunks: List[str] = []
    async for part in router.stream_chat(messages=messages):
        chunks.append(part)

    combined = "".join(chunks)
    assert combined == "Local Ollama fallback answer."
    assert recorded_prompts
    # Prompt should contain the user content in some form.
    assert "Explain fallback behaviour." in recorded_prompts[0]

