from __future__ import annotations

"""Ollama base URL validation: only loopback / container-host http(s) hosts are accepted."""

import json

import httpx
import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from septum_api.routers import settings as settings_router
from septum_api.routers.settings import _validate_ollama_url
from septum_api.services import ollama_client

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
