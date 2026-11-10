"""Contextual layer default at setup completion (settings PATCH).

The server decides ``use_ollama_semantic_layer`` once, when ``setup_completed``
goes false to true. The network is mocked with respx.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from septum_api.database import build_default_app_settings, get_db
from septum_api.main import app
from septum_api.models.regulation import CustomRecognizer, RegulationRuleset
from septum_api.models.settings import AppSettings
from septum_api.models.user import User
from septum_api.utils.auth_dependency import require_admin_or_bootstrap

pytestmark = pytest.mark.respx(assert_all_called=False)

BASE = "http://localhost:11434"
TAGS = f"{BASE}/api/tags"
LOGGER = "septum_api.services.contextual_coverage"


@pytest.fixture(autouse=True)
def _no_ambient_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("USE_OLLAMA_SEMANTIC_LAYER_DEFAULT", raising=False)


async def _session():
    gen = app.dependency_overrides[get_db]()
    session = await gen.__anext__()
    return gen, session


async def _seed(
    *,
    provider: str = "ollama",
    use_layer: bool = True,
    completed: bool = False,
    semantic: bool = False,
    rulesets: list[tuple[str, list[str], bool]] | None = None,
    with_user: bool = False,
    custom: list[tuple[str, bool]] | None = None,
) -> None:
    gen, session = await _session()
    try:
        row = build_default_app_settings()
        row.llm_provider = provider
        row.use_ollama_layer = use_layer
        row.setup_completed = completed
        row.use_ollama_semantic_layer = semantic
        row.ollama_base_url = BASE
        row.ollama_deanon_model = "llama3.2:3b"
        session.add(row)
        for rid, types, active in rulesets if rulesets is not None else [("hp", ["DIAGNOSIS"], True)]:
            session.add(
                RegulationRuleset(
                    id=rid, display_name=rid, region="x", entity_types=types,
                    is_builtin=False, is_active=active,
                )
            )
        for entity_type, active in custom or []:
            session.add(
                CustomRecognizer(
                    name=entity_type, entity_type=entity_type, detection_method="keyword",
                    keywords=["k"], placeholder_label="C", is_active=active,
                )
            )
        if with_user:
            session.add(User(email="admin@example.com", hashed_password="x", role="admin"))
        await session.commit()
    finally:
        await gen.aclose()


async def _stored() -> bool:
    gen, session = await _session()
    try:
        row = await session.get(AppSettings, 1)
        return bool(row.use_ollama_semantic_layer)
    finally:
        await gen.aclose()


def _installed(*names: str) -> httpx.Response:
    return httpx.Response(200, json={"models": [{"name": n} for n in names]})


def _reasons(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage().rsplit(": ", 1)[1]
        for r in caplog.records
        if "Contextual layer default" in r.getMessage()
    ]


async def _complete(client: AsyncClient, **extra: Any) -> httpx.Response:
    return await client.patch("/api/settings", json={"setup_completed": True, **extra})


async def test_enabled_when_every_condition_holds(
    router_client: AsyncClient, respx_mock, caplog: pytest.LogCaptureFixture
) -> None:
    await _seed()
    route = respx_mock.get(TAGS).mock(return_value=_installed("llama3.2:3b"))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        resp = await _complete(router_client)
    assert resp.status_code == 200
    assert resp.json()["use_ollama_semantic_layer"] is True
    assert await _stored() is True
    assert route.call_count == 1
    records = [r for r in caplog.records if "Contextual layer default" in r.getMessage()]
    assert len(records) == 1
    assert records[0].getMessage().endswith(": enabled")
    assert "11434" not in caplog.text and "llama3.2" not in caplog.text


@pytest.mark.parametrize(
    ("effect", "reason"),
    [
        (httpx.ReadTimeout("slow"), "probe_timeout"),
        (httpx.ConnectError("down"), "probe_unreachable"),
        (httpx.Response(503), "probe_http_error"),
        (httpx.Response(200, content=b"not json"), "probe_invalid_response"),
        (httpx.Response(200, json={"models": [{"name": "other:7b"}]}), "probe_model_missing"),
    ],
)
async def test_probe_failure_leaves_layer_off_and_patch_ok(
    router_client: AsyncClient, respx_mock, caplog: pytest.LogCaptureFixture, effect, reason: str
) -> None:
    await _seed()
    mock = respx_mock.get(TAGS)
    if isinstance(effect, Exception):
        mock.mock(side_effect=effect)
    else:
        mock.mock(return_value=effect)
    with caplog.at_level(logging.DEBUG):
        resp = await _complete(router_client)
    assert resp.status_code == 200
    assert resp.json()["setup_completed"] is True
    assert await _stored() is False
    assert _reasons(caplog) == [reason]
    assert all(r.exc_info is None for r in caplog.records)
    assert "Traceback" not in caplog.text
    for text in ("slow", "down", "other:7b", "not json"):
        assert text not in caplog.text


async def test_url_not_allowed_reason(
    router_client: AsyncClient, respx_mock, caplog: pytest.LogCaptureFixture
) -> None:
    await _seed()
    gen, session = await _session()
    try:
        row = await session.get(AppSettings, 1)
        row.ollama_base_url = "http://169.254.169.254"
        await session.commit()
    finally:
        await gen.aclose()
    route = respx_mock.route().mock(return_value=_installed("llama3.2:3b"))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        resp = await _complete(router_client)
    assert resp.status_code == 200
    assert await _stored() is False
    assert _reasons(caplog) == ["probe_url_not_allowed"]
    assert not route.called


@pytest.mark.parametrize(("provider", "use_layer"), [("ollama", False), ("anthropic", True)])
async def test_either_local_model_signal_counts(
    router_client: AsyncClient, respx_mock, provider: str, use_layer: bool
) -> None:
    await _seed(provider=provider, use_layer=use_layer)
    respx_mock.get(TAGS).mock(return_value=_installed("llama3.2:3b"))
    assert (await _complete(router_client)).status_code == 200
    assert await _stored() is True


@pytest.mark.parametrize("semantic", [False, True])
async def test_completed_row_is_never_reevaluated(
    router_client: AsyncClient, respx_mock, semantic: bool
) -> None:
    await _seed(completed=True, semantic=semantic, with_user=True)
    app.dependency_overrides[require_admin_or_bootstrap] = lambda: None
    try:
        route = respx_mock.route().mock(return_value=_installed("llama3.2:3b"))
        resp = await _complete(router_client)
    finally:
        app.dependency_overrides.pop(require_admin_or_bootstrap, None)
    assert resp.status_code == 200
    assert not route.called
    assert await _stored() is semantic


async def test_admin_path_decides_like_bootstrap(router_client: AsyncClient, respx_mock) -> None:
    await _seed(with_user=True)
    app.dependency_overrides[require_admin_or_bootstrap] = lambda: None
    try:
        respx_mock.get(TAGS).mock(return_value=_installed("llama3.2:3b"))
        resp = await _complete(router_client)
    finally:
        app.dependency_overrides.pop(require_admin_or_bootstrap, None)
    assert resp.status_code == 200
    assert await _stored() is True


async def test_anonymous_bootstrap_path_decides(router_client: AsyncClient, respx_mock) -> None:
    await _seed()
    respx_mock.get(TAGS).mock(return_value=_installed("llama3.2:3b"))
    resp = await _complete(router_client)
    assert resp.status_code == 200
    assert await _stored() is True


async def test_bootstrap_without_setup_token_is_refused_and_not_probed(
    router_client: AsyncClient, respx_mock
) -> None:
    await _seed()
    route = respx_mock.route().mock(return_value=_installed("llama3.2:3b"))
    router_client.headers.pop("X-Setup-Token")
    resp = await _complete(router_client)
    assert resp.status_code == 403
    assert not route.called
    assert await _stored() is False


@pytest.mark.parametrize("value", ["false", "0", "off", "No", " false "])
async def test_env_explicit_false_is_an_opt_out(
    router_client: AsyncClient, respx_mock, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("USE_OLLAMA_SEMANTIC_LAYER_DEFAULT", value)
    await _seed()
    route = respx_mock.route().mock(return_value=_installed("llama3.2:3b"))
    resp = await _complete(router_client)
    assert resp.status_code == 200
    assert await _stored() is False
    assert not route.called


async def test_blank_env_is_not_an_opt_out(
    router_client: AsyncClient, respx_mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("USE_OLLAMA_SEMANTIC_LAYER_DEFAULT", "  ")
    await _seed()
    respx_mock.get(TAGS).mock(return_value=_installed("llama3.2:3b"))
    assert (await _complete(router_client)).status_code == 200
    assert await _stored() is True


async def test_explicit_payload_value_is_respected(router_client: AsyncClient, respx_mock) -> None:
    await _seed()
    route = respx_mock.route().mock(return_value=_installed("llama3.2:3b"))
    resp = await _complete(router_client, use_ollama_semantic_layer=False)
    assert resp.status_code == 200
    assert await _stored() is False
    assert not route.called


async def test_settings_update_without_completion_does_not_probe(
    router_client: AsyncClient, respx_mock
) -> None:
    await _seed()
    route = respx_mock.route().mock(return_value=_installed("llama3.2:3b"))
    resp = await router_client.patch("/api/settings", json={"top_k_retrieval": 4})
    assert resp.status_code == 200
    assert not route.called
    assert await _stored() is False


async def test_provider_and_completion_in_one_patch_are_evaluated_together(
    router_client: AsyncClient, respx_mock
) -> None:
    await _seed(provider="anthropic", use_layer=False)
    respx_mock.get(TAGS).mock(return_value=_installed("llama3.2:3b"))
    resp = await _complete(router_client, llm_provider="ollama")
    assert resp.status_code == 200
    assert await _stored() is True


async def test_no_local_model_chosen_means_off_and_no_probe(
    router_client: AsyncClient, respx_mock, caplog: pytest.LogCaptureFixture
) -> None:
    await _seed(provider="anthropic", use_layer=False)
    route = respx_mock.route().mock(return_value=_installed("llama3.2:3b"))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        resp = await _complete(router_client)
    assert resp.status_code == 200
    assert await _stored() is False
    assert not route.called
    assert _reasons(caplog) == ["no_local_model"]


async def test_reason_env_opt_out(
    router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("USE_OLLAMA_SEMANTIC_LAYER_DEFAULT", "false")
    await _seed()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        await _complete(router_client)
    assert _reasons(caplog) == ["env_opt_out"]


async def test_no_contextual_types_means_off_and_no_probe(
    router_client: AsyncClient, respx_mock, caplog: pytest.LogCaptureFixture
) -> None:
    await _seed(rulesets=[("plain", ["PERSON_NAME"], True), ("hp", ["DIAGNOSIS"], False)])
    route = respx_mock.route().mock(return_value=_installed("llama3.2:3b"))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        resp = await _complete(router_client)
    assert resp.status_code == 200
    assert await _stored() is False
    assert not route.called
    assert _reasons(caplog) == ["no_contextual_types"]


async def test_custom_recognizer_alone_enables_the_default(
    router_client: AsyncClient, respx_mock, caplog: pytest.LogCaptureFixture
) -> None:
    await _seed(rulesets=[("plain", ["PERSON_NAME"], True)], custom=[("DIAGNOSIS", True)])
    route = respx_mock.get(TAGS).mock(return_value=_installed("llama3.2:3b"))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        resp = await _complete(router_client)
    assert resp.status_code == 200
    assert await _stored() is True
    assert route.call_count == 1
    assert _reasons(caplog) == ["enabled"]


async def test_row_already_on_before_completion_is_not_probed(
    router_client: AsyncClient, respx_mock
) -> None:
    await _seed(semantic=True, completed=False)
    route = respx_mock.route().mock(return_value=_installed("other:7b"))
    resp = await _complete(router_client)
    assert resp.status_code == 200
    assert await _stored() is True
    assert not route.called


async def test_explicit_true_in_completion_payload_is_stored_without_probe(
    router_client: AsyncClient, respx_mock
) -> None:
    await _seed()
    route = respx_mock.route().mock(return_value=_installed("other:7b"))
    resp = await _complete(router_client, use_ollama_semantic_layer=True)
    assert resp.status_code == 200
    assert await _stored() is True
    assert not route.called
