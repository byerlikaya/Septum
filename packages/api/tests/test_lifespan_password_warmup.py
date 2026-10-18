"""The API lifespan pre-builds the login timing-equalizer hash."""

from __future__ import annotations

import pytest
from septum_api import main
from septum_api.services import auth as auth_service


async def test_lifespan_warms_dummy_password_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(auth_service, "warm_dummy_password_hash", lambda: calls.append("warm"))
    monkeypatch.setattr(main.bootstrap, "needs_setup", lambda: True)

    async with main.lifespan(main.app):
        assert calls == ["warm"]


def test_warm_populates_the_cache() -> None:
    auth_service._dummy_password_hash.cache_clear()
    auth_service.warm_dummy_password_hash()
    assert auth_service._dummy_password_hash.cache_info().currsize == 1
