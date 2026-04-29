"""Tests for /api/auth (register gating, change-password, login)."""

from __future__ import annotations

from httpx import AsyncClient


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
        assert "disabled" in resp.json()["detail"].lower()

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
