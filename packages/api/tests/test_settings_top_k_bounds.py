"""The retrieval top-k setting stays within what one chat turn may retrieve."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from chat_harness import ChatEnv
from httpx import AsyncClient
from septum_api.database import build_default_app_settings
from septum_api.request_limits import TOP_K_RETRIEVAL_MAX_CAP
from septum_api.routers import chat as chat_module


async def _patch(client: AsyncClient, body: dict):
    return await client.patch("/api/settings", json=body)


@pytest.mark.parametrize("value", [0, -1, TOP_K_RETRIEVAL_MAX_CAP + 1, 1_000_000, None])
async def test_out_of_range_top_k_is_422(router_client: AsyncClient, value) -> None:
    before = (await _patch(router_client, {})).json()["top_k_retrieval"]

    resp = await _patch(router_client, {"top_k_retrieval": value})

    assert resp.status_code == 422
    assert (await _patch(router_client, {})).json()["top_k_retrieval"] == before


@pytest.mark.parametrize("value", [1, TOP_K_RETRIEVAL_MAX_CAP])
async def test_in_range_top_k_is_stored(router_client: AsyncClient, value: int) -> None:
    resp = await _patch(router_client, {"top_k_retrieval": value})

    assert resp.status_code == 200
    assert resp.json()["top_k_retrieval"] == value


async def test_update_without_top_k_still_works(router_client: AsyncClient) -> None:
    resp = await _patch(router_client, {"show_json_output": True})

    assert resp.status_code == 200
    assert resp.json()["show_json_output"] is True


@pytest.mark.parametrize(
    "stored,expected",
    [
        (-1, 1),
        (0, 1),
        (1, 1),
        (5, 5),
        (TOP_K_RETRIEVAL_MAX_CAP, TOP_K_RETRIEVAL_MAX_CAP),
        (1_000_000, TOP_K_RETRIEVAL_MAX_CAP),
    ],
)
def test_a_stored_out_of_range_top_k_is_clamped_on_read(stored: int, expected: int) -> None:
    from septum_api.routers.chat import _stored_top_k

    assert _stored_top_k(SimpleNamespace(top_k_retrieval=stored)) == expected


@pytest.mark.parametrize("stored", [-1, 1_000_000])
@pytest.mark.parametrize("auto", [False, True], ids=["manual_retrieval", "auto_retrieval"])
async def test_chat_turn_clamps_a_stored_top_k_before_retrieval(
    chat_env: ChatEnv, monkeypatch: pytest.MonkeyPatch, stored: int, auto: bool
) -> None:
    async with chat_env.session_maker() as session:
        settings = build_default_app_settings()
        settings.top_k_retrieval = stored
        session.add(settings)
        await session.commit()
    doc = await chat_env.add_document(chat_env.users["alice"], "a.pdf", ["alpha"])
    chat_env.act_as("alice")

    seen: list[int] = []
    single_doc = chat_module._retrieve_chunks
    all_docs = chat_module._retrieve_chunks_all_documents

    async def _spy_single(**kwargs):
        seen.append(kwargs["top_k"])
        return await single_doc(**kwargs)

    async def _spy_all(**kwargs):
        seen.append(kwargs["top_k"])
        return await all_docs(**kwargs)

    monkeypatch.setattr(chat_module, "_retrieve_chunks", _spy_single)
    monkeypatch.setattr(chat_module, "_retrieve_chunks_all_documents", _spy_all)

    payload = {"message": "who?"} if auto else {"message": "who?", "document_ids": [doc]}
    status_code, _events, _body = await chat_env.ask(payload)

    assert status_code == 200
    assert len(seen) == 1
    assert 1 <= seen[0] <= TOP_K_RETRIEVAL_MAX_CAP
