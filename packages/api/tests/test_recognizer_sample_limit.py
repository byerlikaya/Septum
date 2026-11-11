"""The recognizer test endpoint refuses oversized sample text before running anything."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from septum_api.request_limits import MAX_PRE_APPROVED_CHUNK_CHARS
from septum_api.routers import regulations as regulations_module


async def test_sample_text_over_the_limit_is_refused_before_the_recognizer_runs(
    router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    registered = await router_client.post(
        "/api/auth/register",
        json={"email": "root@example.com", "password": "password-12345"},
    )
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}

    async def _must_not_run(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("recognizer lookup ran")

    monkeypatch.setattr(regulations_module, "_get_custom_or_404", _must_not_run)

    refused = await router_client.post(
        "/api/regulations/custom/1/test",
        headers=headers,
        json={"sample_text": "a" * (MAX_PRE_APPROVED_CHUNK_CHARS + 1)},
    )

    assert refused.status_code == 422
