"""GET /api/settings/contextual-layer-status: states, probe cache, privacy of the response.

The network is mocked with respx; no request leaves the test process.
"""

from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator

import httpx
import pytest
from fastapi import Request
from httpx import AsyncClient

from septum_api.database import build_default_app_settings, get_db
from septum_api.main import app
from septum_api.models.regulation import CustomRecognizer, RegulationRuleset
from septum_api.models.settings import AppSettings
from septum_api.models.user import User
from septum_api.services import contextual_coverage
from septum_api.utils.auth_dependency import get_current_user

pytestmark = pytest.mark.respx(assert_all_called=False)

BASE = "http://localhost:11434"
TAGS = f"{BASE}/api/tags"
URL = "/api/settings/contextual-layer-status"


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(contextual_coverage, "_probe_cache", {})
    monkeypatch.setattr(contextual_coverage, "_probe_in_flight", {})


@pytest.fixture
async def authed(router_client: AsyncClient) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_current_user] = lambda: _user("admin")
    try:
        yield router_client
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _user(role: str) -> User:
    return User(id=1, email="u@example.com", hashed_password="x", role=role, is_active=True)


async def _update_settings(**fields: object) -> None:
    gen = app.dependency_overrides[get_db]()
    session = await gen.__anext__()
    try:
        row = await session.get(AppSettings, 1)
        if row is None:
            row = build_default_app_settings()
            session.add(row)
        for name, value in fields.items():
            setattr(row, name, value)
        await session.commit()
    finally:
        await gen.aclose()


async def _seed(
    *,
    layer: bool,
    types: list[str] | None = None,
    model: str = "llama3.2:3b",
    base_url: str = BASE,
    custom: tuple[str, bool] | None = None,
) -> None:
    await _update_settings(
        use_ollama_semantic_layer=layer, ollama_base_url=base_url, ollama_deanon_model=model
    )
    gen = app.dependency_overrides[get_db]()
    session = await gen.__anext__()
    try:
        session.add(
            RegulationRuleset(
                id="r", display_name="r", region="x",
                entity_types=["DIAGNOSIS", "RELIGION", "EMAIL_ADDRESS"] if types is None else types,
                is_builtin=False, is_active=True,
            )
        )
        if custom is not None:
            session.add(
                CustomRecognizer(
                    name="c", entity_type=custom[0], detection_method="keyword",
                    keywords=["k"], placeholder_label="C", is_active=custom[1],
                )
            )
        await session.commit()
    finally:
        await gen.aclose()


def _tags(*names: str) -> httpx.Response:
    return httpx.Response(200, json={"models": [{"name": n} for n in names]})


@pytest.mark.parametrize(
    ("layer", "reachable", "state"),
    [
        (True, True, "covered"),
        (True, False, "local_model_unavailable"),
        (False, True, "layer_off"),
        (False, False, "layer_off"),
    ],
)
async def test_state_follows_layer_and_probe(
    authed: AsyncClient, respx_mock, layer: bool, reachable: bool, state: str
) -> None:
    await _seed(layer=layer)
    if reachable:
        respx_mock.get(TAGS).mock(return_value=_tags("llama3.2:3b"))
    else:
        respx_mock.get(TAGS).mock(side_effect=httpx.ConnectError("down"))
    resp = await authed.get(URL)
    assert resp.status_code == 200
    assert resp.json() == {
        "state": state,
        "entity_types": ["DIAGNOSIS", "RELIGION"],
        "local_model_reachable": reachable,
    }


@pytest.mark.parametrize("layer", [True, False])
async def test_not_applicable_without_contextual_types_and_no_probe(
    authed: AsyncClient, respx_mock, layer: bool
) -> None:
    await _seed(layer=layer, types=["EMAIL_ADDRESS"])
    route = respx_mock.get(TAGS).mock(return_value=_tags("llama3.2:3b"))
    body = (await authed.get(URL)).json()
    assert body == {"state": "not_applicable", "entity_types": [], "local_model_reachable": None}
    assert route.call_count == 0


async def test_unauthenticated_is_401(router_client: AsyncClient) -> None:
    resp = await router_client.get(URL)
    assert resp.status_code == 401


async def test_probe_is_cached_within_ttl_and_repeated_after(
    authed: AsyncClient, respx_mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(contextual_coverage, "_monotonic", lambda: clock[0])
    await _seed(layer=True)
    route = respx_mock.get(TAGS).mock(return_value=_tags("llama3.2:3b"))
    await authed.get(URL)
    clock[0] += contextual_coverage.PROBE_CACHE_TTL_SECONDS - 1
    await authed.get(URL)
    assert route.call_count == 1
    clock[0] += 2
    await authed.get(URL)
    assert route.call_count == 2


async def test_cache_key_change_probes_again(authed: AsyncClient, respx_mock) -> None:
    await _seed(layer=True)
    route = respx_mock.get(TAGS).mock(return_value=_tags("llama3.2:3b", "other:7b"))
    await authed.get(URL)
    await _update_settings(ollama_deanon_model="other:7b")
    body = (await authed.get(URL)).json()
    assert route.call_count == 2
    assert body["state"] == "covered"


async def test_hanging_probe_is_bounded_by_the_timeout(
    authed: AsyncClient, respx_mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(contextual_coverage, "PROBE_TIMEOUT_SECONDS", 0.2)
    await _seed(layer=True)

    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(10)
        return _tags("llama3.2:3b")

    respx_mock.get(TAGS).mock(side_effect=hang)
    started = time.monotonic()
    resp = await authed.get(URL)
    assert time.monotonic() - started < 1.5
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "local_model_unavailable"
    assert body["local_model_reachable"] is False


async def test_base_url_change_probes_again(authed: AsyncClient, respx_mock) -> None:
    await _seed(layer=True)
    first = respx_mock.get(TAGS).mock(return_value=_tags("llama3.2:3b"))
    second = respx_mock.get("http://127.0.0.1:11434/api/tags").mock(
        return_value=_tags("llama3.2:3b")
    )
    await authed.get(URL)
    await _update_settings(ollama_base_url="http://127.0.0.1:11434")
    await authed.get(URL)
    assert first.call_count == 1
    assert second.call_count == 1


async def test_negative_result_is_cached_within_ttl(
    authed: AsyncClient, respx_mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(contextual_coverage, "_monotonic", lambda: clock[0])
    await _seed(layer=True)
    route = respx_mock.get(TAGS).mock(side_effect=httpx.ConnectError("down"))
    assert (await authed.get(URL)).json()["state"] == "local_model_unavailable"
    route.mock(return_value=_tags("llama3.2:3b"))
    clock[0] += contextual_coverage.PROBE_CACHE_TTL_SECONDS - 1
    assert (await authed.get(URL)).json()["state"] == "local_model_unavailable"
    assert route.call_count == 1
    clock[0] += 2
    assert (await authed.get(URL)).json()["state"] == "covered"
    assert route.call_count == 2


async def test_layer_toggle_shares_the_cache_key(authed: AsyncClient, respx_mock) -> None:
    await _seed(layer=True)
    route = respx_mock.get(TAGS).mock(return_value=_tags("llama3.2:3b"))
    assert (await authed.get(URL)).json()["state"] == "covered"
    await _update_settings(use_ollama_semantic_layer=False)
    assert (await authed.get(URL)).json()["state"] == "layer_off"
    assert route.call_count == 1


@pytest.mark.parametrize("case", ["covered", "layer_off", "local_model_unavailable", "ssrf"])
async def test_response_never_echoes_url_or_model(
    authed: AsyncClient, respx_mock, case: str
) -> None:
    await _seed(
        layer=case != "layer_off",
        model="secret-model:9b",
        base_url="http://169.254.169.254" if case == "ssrf" else BASE,
    )
    if case == "local_model_unavailable":
        respx_mock.get(TAGS).mock(side_effect=httpx.ConnectError("down"))
    else:
        respx_mock.get(TAGS).mock(return_value=_tags("secret-model:9b"))
    resp = await authed.get(URL)
    assert resp.status_code == 200
    if case == "ssrf":
        assert resp.json()["local_model_reachable"] is False
    for leaked in ("secret-model", "localhost", "11434", "169.254", "http"):
        assert leaked not in resp.text


def _api_key_user(request: Request) -> User:
    request.state.auth_method = "api_key"
    return _user("user")


@pytest.mark.parametrize("override", [lambda: _user("user"), _api_key_user], ids=["user", "api_key"])
async def test_non_admin_callers_get_200(router_client: AsyncClient, respx_mock, override) -> None:
    app.dependency_overrides[get_current_user] = override
    try:
        await _seed(layer=True)
        respx_mock.get(TAGS).mock(return_value=_tags("llama3.2:3b"))
        assert (await router_client.get(URL)).status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_custom_recognizer_alone_makes_the_layer_applicable(
    authed: AsyncClient, respx_mock
) -> None:
    await _seed(layer=False, types=["EMAIL_ADDRESS"], custom=("DIAGNOSIS", True))
    respx_mock.get(TAGS).mock(return_value=_tags("llama3.2:3b"))
    body = (await authed.get(URL)).json()
    assert body["state"] == "layer_off"
    assert body["entity_types"] == ["DIAGNOSIS"]


async def test_concurrent_cold_misses_share_one_probe(
    authed: AsyncClient, respx_mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(layer=True)
    callers = 5
    entered = 0
    all_in_flight = asyncio.Event()
    real = contextual_coverage._local_model_reachable

    async def counting(*args):
        nonlocal entered
        entered += 1
        if entered == callers:
            all_in_flight.set()
        return await real(*args)

    async def gated(request: httpx.Request) -> httpx.Response:
        await all_in_flight.wait()
        return _tags("llama3.2:3b")

    monkeypatch.setattr(contextual_coverage, "_local_model_reachable", counting)
    route = respx_mock.get(TAGS).mock(side_effect=gated)
    responses = await asyncio.gather(*(authed.get(URL) for _ in range(callers)))
    assert all(r.json()["state"] == "covered" for r in responses)
    assert route.call_count == 1
    assert contextual_coverage._probe_in_flight == {}


async def test_cancelled_first_caller_does_not_abort_the_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def fake_probe(settings):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return contextual_coverage.DefaultReason.ENABLED

    monkeypatch.setattr(contextual_coverage, "probe_local_pii_model", fake_probe)
    first = asyncio.create_task(contextual_coverage._local_model_reachable(BASE, "m", None))
    await started.wait()
    second = asyncio.create_task(contextual_coverage._local_model_reachable(BASE, "m", None))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert await second is True
    assert calls == 1
    assert contextual_coverage._probe_in_flight == {}


async def test_failed_probe_clears_the_in_flight_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(settings):
        raise RuntimeError("boom")

    monkeypatch.setattr(contextual_coverage, "probe_local_pii_model", boom)
    with pytest.raises(RuntimeError):
        await contextual_coverage._local_model_reachable(BASE, "m", None)
    assert contextual_coverage._probe_in_flight == {}
