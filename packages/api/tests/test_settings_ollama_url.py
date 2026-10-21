from __future__ import annotations

"""Ollama base URL validation: only loopback / container-host http(s) hosts are accepted."""

import json
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from septum_api import main as main_module
from septum_api.database import get_db
from septum_api.errors import CodedHTTPError
from septum_api.main import app
from septum_api.models.error_log import ErrorLog
from septum_api.models.settings import AppSettings
from septum_api.request_limits import MAX_LLM_MODEL_LENGTH, MAX_LLM_PROVIDER_LENGTH
from septum_api.routers import settings as settings_router
from septum_api.routers.settings import _validate_ollama_url
from septum_api.services import ollama_client
from septum_api.services.ollama_client import OLLAMA_URL_REFUSED_MESSAGE

ALLOWED = [
    "http://localhost:11434",
    "http://127.0.0.1:11434",
    "http://127.10.0.1:11434",
    "http://[::1]:11434",
    "https://[::1]:11434",
    "http://[::ffff:127.0.0.1]:11434",
    "http://host.docker.internal:11434",
    "http://host.containers.internal:11434",
    "http://ollama:11434",
    "HTTP://LOCALHOST:11434",
]

REJECTED = [
    "http://8.8.8.8:11434",
    "http://10.0.0.5:11434",
    "http://172.16.0.2:11434",
    "http://172.31.255.254:11434",
    "http://192.168.1.20:11434",
    "https://[fd12:3456::1]:11434",
    "http://[::ffff:192.168.1.20]:11434",
    "http://172.32.0.1:11434",
    "http://169.254.169.254/latest/meta-data",
    "http://[::ffff:169.254.169.254]",
    "http://0.0.0.0:11434",
    "http://[2001:db8::1]:11434",
    "http://ollama.example.com:11434",
    "http://localhost@attacker.example:11434",
    "file:///etc/passwd",
    "ftp://localhost:11434",
    "gopher://127.0.0.1:11434",
    "localhost:11434",
    "",
    "http://2130706433:11434",
    "http://0x7f.1:11434",
    "http://0x7f000001:11434",
    "http://0177.0.0.1:11434",
    "http://127.1:11434",
    "http://localhost.:11434",
    "http://ollama.:11434",
    "http://user:pw@localhost:11434",
    "http://user@127.0.0.1:11434",
    "http://[::ffff:8.8.8.8]:11434",
    "http://[fe80::1]:11434",
    "http://[fd00:ec2::254]",
    "http://[fd00:ec2::254]:11434/latest/meta-data",
    "http://169.254.169.254:11434",
    "http://localhost:99999",
    "http://10.0.0.5:8500/v1/agent/x#",
    "http://127.0.0.1:11434/x?y#z",
    "http://localhost:11434/api",
    "http://localhost:11434/?x=1",
    "http://localhost:11434/#frag",
    "http://localhost:11434;params",
    "http://localhost:11434/;params",
]


@pytest.mark.parametrize("url", ALLOWED)
def test_allowed_hosts(url: str) -> None:
    assert _validate_ollama_url(url) == url


@pytest.mark.parametrize("url", REJECTED)
def test_rejected_hosts(url: str) -> None:
    with pytest.raises(HTTPException) as exc:
        _validate_ollama_url(url)
    assert exc.value.status_code == 400


async def _admin_headers(client: AsyncClient) -> dict[str, str]:
    resp = await client.post(
        "/api/auth/register",
        json={"email": "root@example.com", "password": "password-12345"},
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def test_patch_rejects_public_ollama_url(router_client: AsyncClient) -> None:
    headers = await _admin_headers(router_client)
    before = (await router_client.get("/api/settings", headers=headers)).json()

    resp = await router_client.patch(
        "/api/settings", headers=headers, json={"ollama_base_url": "http://203.0.113.7:11434"}
    )

    assert resp.status_code == 400
    after = (await router_client.get("/api/settings", headers=headers)).json()
    assert after["ollama_base_url"] == before["ollama_base_url"]


async def test_patch_rejects_private_network_ollama_url(router_client: AsyncClient) -> None:
    headers = await _admin_headers(router_client)
    before = (await router_client.get("/api/settings", headers=headers)).json()

    resp = await router_client.patch(
        "/api/settings", headers=headers, json={"ollama_base_url": "http://192.168.1.20:11434"}
    )

    assert resp.status_code == 400
    after = (await router_client.get("/api/settings", headers=headers)).json()
    assert after["ollama_base_url"] == before["ollama_base_url"]


async def test_patch_accepts_container_host_ollama_url(router_client: AsyncClient) -> None:
    headers = await _admin_headers(router_client)

    resp = await router_client.patch(
        "/api/settings", headers=headers, json={"ollama_base_url": "http://host.docker.internal:11434"}
    )

    assert resp.status_code == 200
    assert resp.json()["ollama_base_url"] == "http://host.docker.internal:11434"


_REAL_ASYNC_CLIENT = httpx.AsyncClient


class _ExplodingClient:
    """httpx client double that fails the test if any request is attempted."""

    def __init__(self, *args, **kwargs) -> None:
        raise AssertionError("no HTTP request may be made to a refused Ollama URL")


@pytest.mark.parametrize(
    "stored_url",
    [
        "http://203.0.113.7:11434",
        "http://[fd00:ec2::254]",
        "http://localhost.",
        "http://10.0.0.5:11434",
    ],
)
async def test_runtime_client_refuses_stored_public_url(
    stored_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ollama_client.httpx, "AsyncClient", _ExplodingClient)
    monkeypatch.setattr(ollama_client.httpx, "Client", _ExplodingClient)

    assert await ollama_client.call_ollama_async("p", base_url=stored_url, model="m") == ""
    assert ollama_client.call_ollama_sync("p", base_url=stored_url, model="m") == ""


async def test_runtime_client_allows_local_url(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"response": "ok"})

    real_client = _REAL_ASYNC_CLIENT
    monkeypatch.setattr(
        ollama_client.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(_handler), **kw),
    )

    out = await ollama_client.call_ollama_async("p", base_url="http://localhost:11434", model="m")

    assert out == "ok"
    assert seen == ["http://localhost:11434/api/generate"]


def _pull_events(body: str) -> list[dict]:
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


async def _pull_with(
    router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch, handler
) -> list[dict]:
    real_client = _REAL_ASYNC_CLIENT
    monkeypatch.setattr(
        settings_router.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    resp = await router_client.post(
        "/api/settings/ollama-pull",
        json={"model": "m", "base_url": "http://localhost:11434"},
    )
    assert resp.status_code == 200
    return _pull_events(resp.text)


async def test_pull_does_not_echo_upstream_status(
    router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = "\n".join(
        json.dumps(line)
        for line in (
            {"status": "pulling secret-upstream-text"},
            {"status": "downloading", "total": 4, "completed": 2},
            {"status": "success"},
        )
    )

    events = await _pull_with(
        router_client, monkeypatch, lambda request: httpx.Response(200, text=upstream)
    )

    assert "secret-upstream-text" not in json.dumps(events)
    assert [e["status"] for e in events] == ["downloading", "downloading", "success"]
    assert events[1]["percent"] == 50
    assert events[-1] == {"status": "success", "percent": 100, "done": True}


async def test_pull_error_is_generic(
    router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret-exception-detail 10.1.2.3")

    events = await _pull_with(router_client, monkeypatch, _fail)
    upstream_error = await _pull_with(
        router_client,
        monkeypatch,
        lambda request: httpx.Response(200, text=json.dumps({"error": "secret-upstream-error"})),
    )

    for evts in (events, upstream_error):
        assert evts == [
            {"status": "Model pull failed.", "percent": 0, "done": True, "error": True}
        ]


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.5:8500/v1/agent/x#",
        "http://127.0.0.1:11434/x?y#z",
        "http://localhost:11434/api",
    ],
)
def test_path_query_and_fragment_are_refused(url: str) -> None:
    assert ollama_client.ollama_url_problem(url) is not None


@pytest.mark.parametrize("url", ["http://localhost:11434", "http://localhost:11434/"])
def test_bare_server_url_is_accepted(url: str) -> None:
    assert ollama_client.ollama_url_problem(url) is None


CODE = "ollama_url_not_allowed"
REFUSED_EXTRA = [
    "http://ollama.local:11434",
    "http://169.254.10.20:11434",
    "https://ollama.example.org",
]


def _assert_coded_400(resp: httpx.Response) -> None:
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["code"] == CODE
    assert isinstance(body["detail"], str)
    assert body["detail"]


@pytest.mark.parametrize("url", REFUSED_EXTRA)
async def test_patch_refusal_carries_code(router_client: AsyncClient, url: str) -> None:
    headers = await _admin_headers(router_client)

    resp = await router_client.patch("/api/settings", headers=headers, json={"ollama_base_url": url})

    _assert_coded_400(resp)


@pytest.mark.parametrize("url", REFUSED_EXTRA)
async def test_test_local_models_refusal_carries_code(
    router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setattr(settings_router.httpx, "AsyncClient", _ExplodingClient)
    headers = await _admin_headers(router_client)

    resp = await router_client.post(
        "/api/settings/test-local-models", headers=headers, json={"base_url": url}
    )

    _assert_coded_400(resp)


@pytest.mark.parametrize("url", REFUSED_EXTRA)
async def test_list_models_refusal_carries_code(
    router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setattr(settings_router.httpx, "AsyncClient", _ExplodingClient)
    headers = await _admin_headers(router_client)

    resp = await router_client.get(
        "/api/settings/ollama-models", headers=headers, params={"base_url": url}
    )

    _assert_coded_400(resp)


@pytest.mark.parametrize("url", REFUSED_EXTRA)
async def test_pull_refusal_carries_code(
    router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setattr(settings_router.httpx, "AsyncClient", _ExplodingClient)
    headers = await _admin_headers(router_client)

    resp = await router_client.post(
        "/api/settings/ollama-pull", headers=headers, json={"model": "m", "base_url": url}
    )

    _assert_coded_400(resp)


@pytest.fixture
def captured_error_logs(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    logged: list[dict] = []

    @asynccontextmanager
    async def _fake_session():
        yield object()

    async def _capture(**kwargs) -> None:
        logged.append(kwargs)

    monkeypatch.setattr(main_module, "engine_is_ready", lambda: True)
    monkeypatch.setattr(main_module, "get_session_maker", lambda: _fake_session)
    monkeypatch.setattr(main_module, "log_backend_message", _capture)
    return logged


async def test_refused_url_error_log_has_no_host(
    router_client: AsyncClient, captured_error_logs: list[dict]
) -> None:
    headers = await _admin_headers(router_client)
    host = "secret-host.example.org"

    resp = await router_client.patch(
        "/api/settings", headers=headers, json={"ollama_base_url": f"https://{host}:11434"}
    )

    assert resp.status_code == 400
    assert len(captured_error_logs) == 1
    entry = captured_error_logs[0]
    assert entry["message"] == OLLAMA_URL_REFUSED_MESSAGE
    assert host not in entry["message"]
    assert entry["status_code"] == 400


async def test_uncoded_4xx_still_logs_its_detail(
    router_client: AsyncClient, captured_error_logs: list[dict]
) -> None:
    headers = await _admin_headers(router_client)
    captured_error_logs.clear()

    resp = await router_client.patch(
        "/api/settings",
        headers=headers,
        json={"default_active_regulations": ["no_such_regulation"]},
    )

    assert resp.status_code == 422
    body = resp.json()
    assert "code" not in body
    assert len(captured_error_logs) == 1
    assert captured_error_logs[0]["message"] == body["detail"]


async def _store_ollama_url(url: str | None) -> None:
    session_gen = app.dependency_overrides[get_db]()
    session = await session_gen.__anext__()
    try:
        await session.execute(update(AppSettings).values(ollama_base_url=url))
        await session.commit()
    finally:
        await session_gen.aclose()


async def test_get_settings_flags_refused_stored_url(router_client: AsyncClient) -> None:
    headers = await _admin_headers(router_client)
    await router_client.get("/api/settings", headers=headers)
    await _store_ollama_url("http://203.0.113.7:11434")

    resp = await router_client.get("/api/settings", headers=headers)

    assert resp.json()["ollama_url_not_allowed"] is True


async def test_get_settings_does_not_flag_allowed_stored_url(router_client: AsyncClient) -> None:
    headers = await _admin_headers(router_client)
    await router_client.get("/api/settings", headers=headers)
    await _store_ollama_url("http://localhost:11434")

    resp = await router_client.get("/api/settings", headers=headers)

    assert resp.json()["ollama_url_not_allowed"] is False


async def test_patch_response_flags_refused_stored_url(router_client: AsyncClient) -> None:
    headers = await _admin_headers(router_client)
    await router_client.get("/api/settings", headers=headers)
    await _store_ollama_url("http://203.0.113.7:11434")

    resp = await router_client.patch("/api/settings", headers=headers, json={"chunk_size": 900})

    assert resp.status_code == 200, resp.text
    assert resp.json()["ollama_url_not_allowed"] is True


async def test_patch_response_does_not_flag_allowed_url(router_client: AsyncClient) -> None:
    headers = await _admin_headers(router_client)

    resp = await router_client.patch(
        "/api/settings", headers=headers, json={"ollama_base_url": "http://localhost:11434"}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["ollama_url_not_allowed"] is False


def test_effective_base_url_prefers_stored_value_and_trims_slash() -> None:
    assert ollama_client.effective_ollama_base_url("http://localhost:1234/") == "http://localhost:1234"


def test_effective_base_url_falls_back_to_env_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama:11434/")

    assert ollama_client.effective_ollama_base_url(None) == "http://ollama:11434"


async def _test_llm_with_router_error(
    router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> httpx.Response:
    async def _raise(self, *args, **kwargs):
        raise error

    monkeypatch.setattr(settings_router.LLMRouter, "complete", _raise)
    headers = await _admin_headers(router_client)
    return await router_client.post(
        "/api/settings/test-llm", headers=headers, json={"provider": "ollama"}
    )


async def test_test_llm_coded_router_error_carries_code(
    router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = settings_router.LLMRouterError("refused", code=CODE)

    resp = await _test_llm_with_router_error(router_client, monkeypatch, error)

    _assert_coded_400(resp)


async def test_test_llm_uncoded_router_error_hides_provider_text(
    router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider_text = "upstream failed at http://10.0.0.5:8500/v1/chat"
    error = settings_router.LLMRouterError(provider_text)
    probe_gen = app.dependency_overrides[get_db]()
    probe = await probe_gen.__anext__()
    session_maker = async_sessionmaker(bind=probe.bind, expire_on_commit=False)
    await probe_gen.aclose()
    monkeypatch.setattr(main_module, "engine_is_ready", lambda: True)
    monkeypatch.setattr(main_module, "get_session_maker", lambda: session_maker)

    resp = await _test_llm_with_router_error(router_client, monkeypatch, error)

    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "llm_unavailable"
    assert body["detail"] == settings_router.LLM_TEST_UNAVAILABLE_MESSAGE
    assert provider_text not in resp.text
    session_gen = app.dependency_overrides[get_db]()
    session = await session_gen.__anext__()
    try:
        rows = (await session.execute(select(ErrorLog))).scalars().all()
    finally:
        await session_gen.aclose()
    # The route writes no row of its own; the HTTPException handler records the 400 once.
    assert len(rows) == 1
    assert not any(row.exception_type == "LLMRouterError" for row in rows)
    for row in rows:
        assert provider_text not in row.message
        assert provider_text not in (row.stack_trace or "")


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("post", "/api/settings/test-local-models", {"json": {"base_url": "http://localhost:11434"}}),
        ("get", "/api/settings/ollama-probe", {}),
        ("get", "/api/settings/ollama-models", {"params": {"base_url": "http://localhost:11434"}}),
        ("post", "/api/settings/ollama-pull", {"json": {"model": "m", "base_url": "http://localhost:11434"}}),
    ],
)
async def test_settings_ollama_clients_disable_env_proxies(
    router_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    kwargs: dict,
) -> None:
    seen: list[dict] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/pull":
            return httpx.Response(200, text=json.dumps({"status": "success"}))
        return httpx.Response(200, json={"models": [{"name": "m"}]})

    def _recording_client(**kw):
        seen.append(dict(kw))
        return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(_handler), **kw)

    monkeypatch.setattr(settings_router.httpx, "AsyncClient", _recording_client)
    headers = await _admin_headers(router_client)

    resp = await getattr(router_client, method)(path, headers=headers, **kwargs)

    assert resp.status_code == 200, resp.text
    assert len(seen) == 1
    assert seen[0].get("trust_env") is False


def test_coded_http_error_log_message_defaults_to_safe_text() -> None:
    error = CodedHTTPError(400, "detail with secret-host.example.org", "some_code")

    assert error.log_message == "Request refused (code: some_code)"


async def test_test_llm_coded_refusal_persists_host_free_message(
    router_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    captured_error_logs: list[dict],
) -> None:
    headers = await _admin_headers(router_client)
    captured_error_logs.clear()
    error = settings_router.LLMRouterError(OLLAMA_URL_REFUSED_MESSAGE, code=CODE)

    async def _raise(self, *args, **kwargs):
        raise error

    monkeypatch.setattr(settings_router.LLMRouter, "complete", _raise)
    resp = await router_client.post(
        "/api/settings/test-llm", headers=headers, json={"provider": "ollama"}
    )

    assert resp.status_code == 400
    assert [e["message"] for e in captured_error_logs] == [f"Request refused (code: {CODE})"]
    assert "secret-host" not in captured_error_logs[0]["message"]


async def test_coded_error_without_log_message_persists_safe_text_not_detail(
    router_client: AsyncClient, captured_error_logs: list[dict]
) -> None:
    async def _boom() -> None:
        raise CodedHTTPError(400, "detail with secret-host.example.org", "some_code")

    app.add_api_route("/api/_coded_probe", _boom, methods=["GET"])
    try:
        captured_error_logs.clear()
        resp = await router_client.get("/api/_coded_probe")
    finally:
        app.router.routes.pop()

    assert resp.status_code == 400
    assert resp.json()["code"] == "some_code"
    assert [e["message"] for e in captured_error_logs] == ["Request refused (code: some_code)"]


async def test_test_llm_unexpected_error_is_generic(
    router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    resp = await _test_llm_with_router_error(
        router_client, monkeypatch, RuntimeError("secret http://10.0.0.5")
    )

    assert resp.status_code == 400
    assert "secret" not in resp.text
    assert "10.0.0.5" not in resp.text
    assert (
        resp.json()["detail"]
        == "Cloud LLM connection test failed due to an unexpected error."
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"provider": "p" * (MAX_LLM_PROVIDER_LENGTH + 1)},
        {"provider": "ollama", "model": "m" * (MAX_LLM_MODEL_LENGTH + 1)},
    ],
)
async def test_test_llm_rejects_over_limit_fields(
    router_client: AsyncClient, payload: dict[str, str]
) -> None:
    headers = await _admin_headers(router_client)

    resp = await router_client.post("/api/settings/test-llm", headers=headers, json=payload)

    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize(
    "payload",
    [
        {"llm_provider": "p" * (MAX_LLM_PROVIDER_LENGTH + 1)},
        {"llm_model": "m" * (MAX_LLM_MODEL_LENGTH + 1)},
    ],
)
async def test_patch_rejects_over_limit_llm_fields(
    router_client: AsyncClient, payload: dict[str, str]
) -> None:
    headers = await _admin_headers(router_client)

    resp = await router_client.patch("/api/settings", headers=headers, json=payload)

    assert resp.status_code == 422, resp.text


async def test_patch_accepts_llm_fields_at_limit(router_client: AsyncClient) -> None:
    headers = await _admin_headers(router_client)
    model = "m" * MAX_LLM_MODEL_LENGTH

    resp = await router_client.patch("/api/settings", headers=headers, json={"llm_model": model})

    assert resp.status_code == 200, resp.text
    assert resp.json()["llm_model"] == model


async def test_test_llm_accepts_fields_at_limit(
    router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _ok(self, *args, **kwargs) -> str:
        return "ok"

    monkeypatch.setattr(settings_router.LLMRouter, "complete", _ok)
    headers = await _admin_headers(router_client)

    resp = await router_client.post(
        "/api/settings/test-llm",
        headers=headers,
        json={"provider": "p" * MAX_LLM_PROVIDER_LENGTH, "model": "m" * MAX_LLM_MODEL_LENGTH},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True


def _unreachable_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every probe target refuse the connection."""

    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    def _client(**kw):
        return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(_handler), **kw)

    monkeypatch.setattr(settings_router.httpx, "AsyncClient", _client)


async def test_ollama_probe_returns_default_when_no_target_answers(
    router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("DOCKER", raising=False)
    monkeypatch.setattr("septum_api.config._is_docker", lambda: False)
    _unreachable_ollama(monkeypatch)

    resp = await router_client.get("/api/settings/ollama-probe")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"url": None, "models": [], "default_url": "http://localhost:11434"}


async def test_ollama_probe_default_reflects_env(
    router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama:11434/")
    _unreachable_ollama(monkeypatch)

    resp = await router_client.get("/api/settings/ollama-probe")

    assert resp.json()["default_url"] == "http://ollama:11434"


async def test_ollama_probe_default_reflects_docker_fallback(
    router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.setenv("DOCKER", "true")
    _unreachable_ollama(monkeypatch)

    resp = await router_client.get("/api/settings/ollama-probe")

    assert resp.json()["default_url"] == "http://host.docker.internal:11434"


@pytest.mark.parametrize(
    "configured",
    ["http://10.0.0.5:11434", "http://user:pw@localhost:11434", "http://169.254.169.254", ""],
)
async def test_ollama_probe_invalid_configured_default_is_null(
    router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch, configured: str
) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", configured)
    _unreachable_ollama(monkeypatch)

    resp = await router_client.get("/api/settings/ollama-probe")

    assert resp.status_code == 200
    assert resp.json()["default_url"] is None
    if configured:
        assert configured not in resp.text


async def test_ollama_probe_reachable_target_keeps_fields_and_adds_default(
    router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama:11434")

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "m"}]})

    monkeypatch.setattr(
        settings_router.httpx,
        "AsyncClient",
        lambda **kw: _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(_handler), **kw),
    )

    resp = await router_client.get("/api/settings/ollama-probe")

    assert resp.json() == {
        "url": "http://host.docker.internal:11434",
        "models": ["m"],
        "default_url": "http://ollama:11434",
    }


async def test_ollama_probe_requires_setup_token_during_setup(
    router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama:11434")
    _unreachable_ollama(monkeypatch)

    resp = await router_client.get(
        "/api/settings/ollama-probe", headers={"X-Setup-Token": "wrong-token-0123456789abcdefgh"}
    )
    assert resp.status_code in (401, 403)
    assert "default_url" not in resp.text


async def test_ollama_probe_requires_admin_after_setup(
    router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _admin_headers(router_client)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama:11434")
    _unreachable_ollama(monkeypatch)

    resp = await router_client.get("/api/settings/ollama-probe")

    assert resp.status_code == 401
    assert "default_url" not in resp.text


def test_ollama_probe_dependency_and_schema() -> None:
    route = next(
        r
        for r in settings_router.router.routes
        if getattr(r, "endpoint", None) is settings_router.ollama_probe_endpoint
    )
    calls = [d.call for d in route.dependant.dependencies]
    assert settings_router.require_admin_or_bootstrap in calls
    props = app.openapi()["components"]["schemas"]["OllamaProbeResponse"]["properties"]
    assert set(props) == {"url", "models", "default_url"}
