"""Refused Ollama base URLs raise a coded, host-free error before any call is made."""

from __future__ import annotations

import pytest
from factories.app_settings_factory import make_app_settings
from septum_api.services import ollama_client
from septum_api.services.ollama_client import (
    OLLAMA_URL_REFUSED_MESSAGE,
    require_allowed_ollama_url,
)
from septum_api.services.llm_providers import health
from septum_api.services.llm_providers.factory import validate_provider_config
from septum_api.services.llm_router import LLMRouter, LLMRouterError

REFUSED_URL = "http://secret-host.example.org:11434"
CODE = "ollama_url_not_allowed"


def _ollama_settings(url: str | None, use_gateway: bool = False):
    settings = make_app_settings(provider="ollama", model="llama3.2:3b")
    settings.ollama_base_url = url
    settings.use_gateway = use_gateway
    return settings


@pytest.fixture
def ollama_calls(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    calls: list[str | None] = []

    async def _fake(prompt: str, base_url: str | None = None, **kwargs) -> str:
        calls.append(base_url)
        return "local answer"

    monkeypatch.setattr(ollama_client, "call_ollama_async", _fake)
    return calls


@pytest.fixture(autouse=True)
def _reset_breaker():
    health.reset("ollama:llama3.2:3b")
    yield
    health.reset("ollama:llama3.2:3b")


def test_ensure_returns_effective_url_for_allowed_host() -> None:
    assert require_allowed_ollama_url("http://localhost:11434/") == "http://localhost:11434"


def test_ensure_raises_coded_error_for_refused_host() -> None:
    with pytest.raises(LLMRouterError) as exc:
        require_allowed_ollama_url(REFUSED_URL)

    assert exc.value.code == CODE


def test_ensure_error_message_leaks_no_host() -> None:
    with pytest.raises(LLMRouterError) as exc:
        require_allowed_ollama_url(REFUSED_URL)

    assert str(exc.value) == OLLAMA_URL_REFUSED_MESSAGE
    assert "secret-host" not in str(exc.value)
    assert "example.org" not in str(exc.value)


def test_validate_provider_config_raises_coded_error_for_refused_ollama_url() -> None:
    with pytest.raises(LLMRouterError) as exc:
        validate_provider_config(_ollama_settings(REFUSED_URL))

    assert exc.value.code == CODE


def test_validate_provider_config_accepts_allowed_ollama_url() -> None:
    validate_provider_config(_ollama_settings("http://localhost:11434"))


def test_cloud_provider_validate_config_is_a_noop_despite_refused_ollama_url() -> None:
    settings = make_app_settings(provider="anthropic", model="claude-test")
    settings.ollama_base_url = REFUSED_URL

    validate_provider_config(settings)


def test_uncoded_router_error_has_no_code() -> None:
    assert LLMRouterError("boom").code is None


async def test_provider_complete_refuses_before_calling_ollama(ollama_calls: list) -> None:
    router = LLMRouter(settings=_ollama_settings(REFUSED_URL))

    with pytest.raises(LLMRouterError) as exc:
        await router.complete(messages=[{"role": "user", "content": "hi"}])

    assert exc.value.code == CODE
    assert ollama_calls == []


async def test_stream_chat_refuses_even_with_breaker_open(ollama_calls: list) -> None:
    for _ in range(20):
        health.record_failure("ollama:llama3.2:3b")
    assert not health.is_available("ollama:llama3.2:3b")
    router = LLMRouter(settings=_ollama_settings(REFUSED_URL))

    with pytest.raises(LLMRouterError) as exc:
        async for _ in router.stream_chat(messages=[{"role": "user", "content": "hi"}]):
            pass

    assert exc.value.code == CODE
    assert ollama_calls == []


async def test_stream_chat_refusal_records_no_failure(ollama_calls: list) -> None:
    router = LLMRouter(settings=_ollama_settings(REFUSED_URL))

    with pytest.raises(LLMRouterError):
        async for _ in router.stream_chat(messages=[{"role": "user", "content": "hi"}]):
            pass

    assert health.get_provider_status("ollama:llama3.2:3b")["total_failures"] == 0


async def test_stream_chat_allows_loopback_url(ollama_calls: list) -> None:
    router = LLMRouter(settings=_ollama_settings("http://localhost:11434"))

    text = "".join([p async for p in router.stream_chat(messages=[{"role": "user", "content": "hi"}])])

    assert text == "local answer"


class _FakeGateway:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, **kwargs) -> str:
        self.calls += 1
        return "gateway answer"


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> _FakeGateway:
    from septum_api.services import llm_router

    fake = _FakeGateway()
    monkeypatch.setattr(llm_router, "_gateway_client_factory", lambda settings: fake)
    return fake


async def test_stream_chat_refuses_refused_url_even_with_gateway_on(
    ollama_calls: list, gateway: _FakeGateway
) -> None:
    router = LLMRouter(settings=_ollama_settings(REFUSED_URL, use_gateway=True))

    with pytest.raises(LLMRouterError) as exc:
        async for _ in router.stream_chat(messages=[{"role": "user", "content": "hi"}]):
            pass

    assert exc.value.code == CODE
    assert gateway.calls == 0
    assert ollama_calls == []
    assert health.get_provider_status("ollama:llama3.2:3b")["total_failures"] == 0


async def test_stream_chat_cloud_provider_still_uses_gateway(gateway: _FakeGateway) -> None:
    settings = make_app_settings(provider="anthropic", model="claude-test")
    settings.use_gateway = True
    settings.ollama_base_url = REFUSED_URL
    health.reset("anthropic:claude-test")
    router = LLMRouter(settings=settings)

    text = "".join([p async for p in router.stream_chat(messages=[{"role": "user", "content": "hi"}])])

    assert text == "gateway answer"
    assert gateway.calls == 1
    health.reset("anthropic:claude-test")


@pytest.mark.parametrize("use_async", [False, True])
async def test_ollama_clients_disable_env_proxies(
    monkeypatch: pytest.MonkeyPatch, use_async: bool
) -> None:
    import httpx

    seen: list[dict] = []

    class _Resp:
        def raise_for_status(self) -> None: ...
        def json(self) -> dict:
            return {"response": "ok"}

    class _Client:
        def __init__(self, **kwargs) -> None:
            seen.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *a) -> None: ...

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a) -> None: ...

        def post(self, *a, **k) -> _Resp:
            return _Resp()

    class _AsyncClient(_Client):
        async def post(self, *a, **k) -> _Resp:
            return _Resp()

    monkeypatch.setattr(httpx, "Client", _Client)
    monkeypatch.setattr(httpx, "AsyncClient", _AsyncClient)

    if use_async:
        await ollama_client.call_ollama_async("p", base_url="http://localhost:11434")
    else:
        ollama_client.call_ollama_sync("p", base_url="http://localhost:11434")

    assert len(seen) == 1
    assert seen[0]["trust_env"] is False
