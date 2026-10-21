"""Tests for /api/auth (register gating, change-password, login)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import AsyncClient
from septum_api import bootstrap
from septum_api.routers.auth import _insert_first_admin
from septum_api.utils.auth_dependency import SETUP_FORBIDDEN_DETAIL

_ROOT = {"email": "root@example.com", "password": "password-12345"}


async def _register_first(client: AsyncClient, email: str, password: str) -> str:
    """Register the initial user and return the issued access token."""
    resp = await client.post(
        "/api/auth/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


class TestRegisterGating:
    async def test_first_user_becomes_admin(self, router_client: AsyncClient) -> None:
        token = await _register_first(router_client, "root@example.com", "password-12345")

        me = await router_client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert me.status_code == 200
        body = me.json()
        assert body["email"] == "root@example.com"
        assert body["role"] == "admin"
        assert body["is_active"] is True

    async def test_second_register_is_forbidden(self, router_client: AsyncClient) -> None:
        await _register_first(router_client, "root@example.com", "password-12345")

        resp = await router_client.post(
            "/api/auth/register",
            json={"email": "someone@example.com", "password": "password-12345"},
        )
        assert resp.status_code == 403
        assert resp.json() == {"detail": SETUP_FORBIDDEN_DETAIL, "code": "setup_forbidden"}

    @pytest.mark.parametrize("header", [None, "wrong-token"])
    async def test_register_without_valid_setup_token_is_403(
        self, router_client: AsyncClient, header: str | None
    ) -> None:
        router_client.headers.pop("X-Setup-Token")
        headers = {"X-Setup-Token": header} if header else {}
        resp = await router_client.post("/api/auth/register", json=_ROOT, headers=headers)
        assert resp.status_code == 403
        assert resp.json()["detail"] == SETUP_FORBIDDEN_DETAIL

    async def test_token_no_longer_accepted_once_setup_is_closed(
        self, router_client: AsyncClient
    ) -> None:
        await _register_first(router_client, **_ROOT)
        resp = await router_client.post(
            "/api/auth/register",
            json={"email": "late@example.com", "password": "password-12345"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == SETUP_FORBIDDEN_DETAIL

    async def test_concurrent_registers_create_exactly_one_admin(
        self, router_client: AsyncClient
    ) -> None:
        responses = await asyncio.gather(*(
            router_client.post(
                "/api/auth/register",
                json={"email": f"user{i}@example.com", "password": "password-12345"},
            )
            for i in range(5)
        ))
        codes = sorted(r.status_code for r in responses)
        assert codes == [201, 403, 403, 403, 403]

    async def test_register_deletes_the_stored_setup_token(
        self, router_client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SEPTUM_CONFIG_PATH", str(tmp_path / "config.json"))
        monkeypatch.delenv(bootstrap.SETUP_TOKEN_ENV)
        bootstrap._invalidate_cache()
        stored = bootstrap.ensure_setup_token()
        resp = await router_client.post(
            "/api/auth/register", json=_ROOT, headers={"X-Setup-Token": stored}
        )
        assert resp.status_code == 201
        assert bootstrap.get_setup_token() is None
        assert stored not in resp.text
        bootstrap._invalidate_cache()


    async def test_concurrent_registers_without_the_process_lock_still_create_one_admin(
        self, router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The conditional insert alone must hold when requests come from separate workers."""
        import contextlib

        from septum_api.routers import auth as auth_router

        monkeypatch.setattr(auth_router, "_first_admin_lock", contextlib.nullcontext())
        responses = await asyncio.gather(*(
            router_client.post(
                "/api/auth/register",
                json={"email": f"worker{i}@example.com", "password": "password-12345"},
            )
            for i in range(5)
        ))
        assert sorted(r.status_code for r in responses) == [201, 403, 403, 403, 403]
        winner = next(r for r in responses if r.status_code == 201)
        me = await router_client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {winner.json()['access_token']}"},
        )
        assert me.status_code == 200
        assert me.json()["role"] == "admin"


class TestRegisterRateLimit:
    async def test_rejected_setup_tokens_count_toward_the_limit(
        self, router_client: AsyncClient
    ) -> None:
        from septum_api.main import limiter

        router_client.headers["X-Setup-Token"] = "guess"
        limiter.reset()
        limiter.enabled = True
        try:
            codes = [
                (await router_client.post("/api/auth/register", json=_ROOT)).status_code
                for _ in range(4)
            ]
        finally:
            limiter.enabled = False
            limiter.reset()
        assert codes == [403, 403, 403, 429]


class TestFirstAdminInsert:
    async def test_second_insert_is_refused_without_the_lock(self, tmp_path: Path) -> None:
        from septum_api.models import Base
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'race.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
        try:
            async with session_maker() as first, session_maker() as second:
                assert await _insert_first_admin(first, "a@example.com", "h") is True
                await first.commit()
                assert await _insert_first_admin(second, "b@example.com", "h") is False
        finally:
            await engine.dispose()

    async def test_register_rejects_short_password(self, router_client: AsyncClient) -> None:
        resp = await router_client.post(
            "/api/auth/register",
            json={"email": "root@example.com", "password": "short"},
        )
        assert resp.status_code == 400
        assert "12 characters" in resp.json()["detail"]


class TestLogin:
    async def test_login_happy_path(self, router_client: AsyncClient) -> None:
        await _register_first(router_client, "root@example.com", "password-12345")
        resp = await router_client.post(
            "/api/auth/login",
            json={"email": "root@example.com", "password": "password-12345"},
        )
        assert resp.status_code == 200
        assert resp.json()["token_type"] == "bearer"

    async def test_login_wrong_password(self, router_client: AsyncClient) -> None:
        await _register_first(router_client, "root@example.com", "password-12345")
        resp = await router_client.post(
            "/api/auth/login",
            json={"email": "root@example.com", "password": "wrong-password-1"},
        )
        assert resp.status_code == 401

    async def test_login_unknown_email(self, router_client: AsyncClient) -> None:
        resp = await router_client.post(
            "/api/auth/login",
            json={"email": "ghost@example.com", "password": "password-12345"},
        )
        assert resp.status_code == 401


class TestChangePassword:
    async def test_happy_path_rotates_token_and_password(
        self, router_client: AsyncClient
    ) -> None:
        token = await _register_first(router_client, "root@example.com", "old-password-12345")
        headers = {"Authorization": f"Bearer {token}"}

        resp = await router_client.post(
            "/api/auth/change-password",
            headers=headers,
            json={
                "current_password": "old-password-12345",
                "new_password": "new-password-12345",
            },
        )
        assert resp.status_code == 200
        new_token = resp.json()["access_token"]
        assert new_token

        old_login = await router_client.post(
            "/api/auth/login",
            json={"email": "root@example.com", "password": "old-password-12345"},
        )
        assert old_login.status_code == 401

        new_login = await router_client.post(
            "/api/auth/login",
            json={"email": "root@example.com", "password": "new-password-12345"},
        )
        assert new_login.status_code == 200

    async def test_rejects_wrong_current_password(self, router_client: AsyncClient) -> None:
        token = await _register_first(router_client, "root@example.com", "password-12345")
        resp = await router_client.post(
            "/api/auth/change-password",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "current_password": "nope",
                "new_password": "new-password-12345",
            },
        )
        assert resp.status_code == 400

    async def test_rejects_short_new_password(self, router_client: AsyncClient) -> None:
        token = await _register_first(router_client, "root@example.com", "password-12345")
        resp = await router_client.post(
            "/api/auth/change-password",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "current_password": "password-12345",
                "new_password": "short",
            },
        )
        assert resp.status_code == 400

    async def test_requires_auth(self, router_client: AsyncClient) -> None:
        resp = await router_client.post(
            "/api/auth/change-password",
            json={
                "current_password": "whatever",
                "new_password": "new-password-12345",
            },
        )
        assert resp.status_code == 401
