"""Bounds on the chat request's ``top_k``: None and 0 mean "use the settings default"."""

from __future__ import annotations

import pytest
from chat_harness import ChatEnv
from pydantic import ValidationError
from septum_api.request_limits import TOP_K_RETRIEVAL_MAX_CAP
from septum_api.routers.chat import ChatRequest


@pytest.mark.parametrize("value", [None, 0, 1, TOP_K_RETRIEVAL_MAX_CAP])
def test_top_k_in_range_is_accepted(value: int | None) -> None:
    assert ChatRequest(message="hi", top_k=value).top_k == value


@pytest.mark.parametrize("value", [TOP_K_RETRIEVAL_MAX_CAP + 1, 1_000_000, -1])
def test_top_k_out_of_range_is_rejected(value: int) -> None:
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", top_k=value)


class TestChatTopKEndpoint:
    @pytest.mark.parametrize("value", [TOP_K_RETRIEVAL_MAX_CAP + 1, -1])
    async def test_out_of_range_top_k_is_422(self, chat_env: ChatEnv, value: int) -> None:
        doc = await chat_env.add_document(chat_env.users["alice"], "a.pdf", ["alpha"])
        chat_env.act_as("alice")
        status_code, events, body = await chat_env.ask(
            {"message": "who?", "document_ids": [doc], "top_k": value}
        )
        assert status_code == 422
        assert events == []
        assert any("top_k" in item["loc"] for item in body["detail"])
