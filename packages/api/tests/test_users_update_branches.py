"""Characterization of the PATCH /api/users/{id} branches (pins behaviour before a refactor)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from septum_api.routers import users as users_router


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _bootstrap_admin(client: AsyncClient, email: str = "root@example.com") -> str:
    resp = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "password-12345"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


async def _create_user(
    client: AsyncClient,
    admin_token: str,
    email: str,
    role: str = "editor",
    is_active: bool = True,
) -> dict:
    resp = await client.post(
        "/api/users",
        headers=_auth(admin_token),
        json={"email": email, "password": "password-12345", "role": role, "is_active": is_active},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _patch(client: AsyncClient, token: str, user_id: int, body: dict):
    return await client.patch(f"/api/users/{user_id}", headers=_auth(token), json=body)


def _admin_count_must_not_run(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock(side_effect=AssertionError("last-admin count must not run"))
    monkeypatch.setattr(users_router, "_count_active_admins", mock)
    return mock


def _admin_count_spy(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock(wraps=users_router._count_active_admins)
    monkeypatch.setattr(users_router, "_count_active_admins", mock)
    return mock


class TestUpdateUserBranches:
    async def test_demoting_the_last_active_admin_is_409(
        self, router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        admin_token = await _bootstrap_admin(router_client)
        target = await _create_user(router_client, admin_token, "b@example.com", role="admin")
        monkeypatch.setattr(users_router, "_count_active_admins", AsyncMock(return_value=0))

        resp = await _patch(router_client, admin_token, target["id"], {"role": "editor"})

        assert resp.status_code == 409
        assert resp.json()["detail"] == "Cannot demote the last active administrator"

    async def test_deactivating_the_last_active_admin_is_409(
        self, router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        admin_token = await _bootstrap_admin(router_client)
        target = await _create_user(router_client, admin_token, "b@example.com", role="admin")
        monkeypatch.setattr(users_router, "_count_active_admins", AsyncMock(return_value=0))

        resp = await _patch(router_client, admin_token, target["id"], {"is_active": False})

        assert resp.status_code == 409
        assert resp.json()["detail"] == "Cannot deactivate the last active administrator"

    async def test_demoting_an_admin_while_another_admin_exists_is_200(
        self, router_client: AsyncClient
    ) -> None:
        admin_token = await _bootstrap_admin(router_client)
        target = await _create_user(router_client, admin_token, "b@example.com", role="admin")

        resp = await _patch(router_client, admin_token, target["id"], {"role": "viewer"})

        assert resp.status_code == 200
        assert resp.json()["role"] == "viewer"

    async def test_deactivating_an_admin_while_another_admin_exists_is_200(
        self, router_client: AsyncClient
    ) -> None:
        admin_token = await _bootstrap_admin(router_client)
        target = await _create_user(router_client, admin_token, "b@example.com", role="admin")

        resp = await _patch(router_client, admin_token, target["id"], {"is_active": False})

        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    async def test_role_change_of_a_non_admin_is_recounted_and_succeeds(
        self, router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        admin_token = await _bootstrap_admin(router_client)
        target = await _create_user(router_client, admin_token, "e@example.com", role="editor")
        count = _admin_count_spy(monkeypatch)

        resp = await _patch(router_client, admin_token, target["id"], {"role": "viewer"})

        assert resp.status_code == 200
        count.assert_awaited_once()

    async def test_deactivating_a_non_admin_is_recounted_and_succeeds(
        self, router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        admin_token = await _bootstrap_admin(router_client)
        target = await _create_user(router_client, admin_token, "e@example.com", role="editor")
        count = _admin_count_spy(monkeypatch)

        resp = await _patch(router_client, admin_token, target["id"], {"is_active": False})

        assert resp.status_code == 200
        count.assert_awaited_once()

    async def test_promoting_never_counts_admins(
        self, router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        admin_token = await _bootstrap_admin(router_client)
        target = await _create_user(router_client, admin_token, "e@example.com", role="editor")
        count = _admin_count_must_not_run(monkeypatch)

        resp = await _patch(router_client, admin_token, target["id"], {"role": "admin", "is_active": True})

        assert resp.status_code == 200
        count.assert_not_awaited()

    async def test_keeping_an_admin_role_never_counts_admins(
        self, router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        admin_token = await _bootstrap_admin(router_client)
        target = await _create_user(router_client, admin_token, "b@example.com", role="admin")
        count = _admin_count_must_not_run(monkeypatch)

        resp = await _patch(router_client, admin_token, target["id"], {"role": "admin"})

        assert resp.status_code == 200
        count.assert_not_awaited()

    async def test_patching_an_already_inactive_admin_is_recounted_and_succeeds(
        self, router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        admin_token = await _bootstrap_admin(router_client)
        target = await _create_user(
            router_client, admin_token, "b@example.com", role="admin", is_active=False
        )
        count = _admin_count_spy(monkeypatch)

        resp = await _patch(router_client, admin_token, target["id"], {"is_active": False})

        assert resp.status_code == 200
        count.assert_awaited_once()

    async def test_unchanged_email_skips_the_duplicate_check(
        self, router_client: AsyncClient
    ) -> None:
        admin_token = await _bootstrap_admin(router_client)
        target = await _create_user(router_client, admin_token, "e@example.com")

        resp = await _patch(router_client, admin_token, target["id"], {"email": "e@example.com"})

        assert resp.status_code == 200
        assert resp.json()["email"] == "e@example.com"

    async def test_empty_patch_returns_the_user_unchanged(self, router_client: AsyncClient) -> None:
        admin_token = await _bootstrap_admin(router_client)
        target = await _create_user(router_client, admin_token, "e@example.com", role="viewer")

        resp = await _patch(router_client, admin_token, target["id"], {})

        assert resp.status_code == 200
        assert resp.json()["role"] == "viewer"
        assert resp.json()["email"] == "e@example.com"
