"""Tests for /api/users admin-scoped user management."""

from __future__ import annotations

from pathlib import Path

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from septum_api.models import Base
from septum_api.models.user import User


async def _bootstrap_admin(
    client: AsyncClient, email: str = "root@example.com", password: str = "password-12345"
) -> str:
    """Register the first (admin) user and return their token."""
    resp = await client.post(
        "/api/auth/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_user(
    client: AsyncClient,
    admin_token: str,
    email: str,
    password: str = "password-12345",
    role: str = "editor",
    is_active: bool = True,
) -> dict:
    resp = await client.post(
        "/api/users",
        headers=_auth(admin_token),
        json={
            "email": email,
            "password": password,
            "role": role,
            "is_active": is_active,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _login(client: AsyncClient, email: str, password: str) -> str:
    resp = await client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


class TestAdminGate:
    async def test_unauthenticated_requests_are_rejected(
        self, router_client: AsyncClient
    ) -> None:
        resp = await router_client.get("/api/users")
        assert resp.status_code == 401

    async def test_non_admin_cannot_list_users(self, router_client: AsyncClient) -> None:
        admin_token = await _bootstrap_admin(router_client)
        await _create_user(router_client, admin_token, "editor@example.com", role="editor")
        editor_token = await _login(router_client, "editor@example.com", "password-12345")

        resp = await router_client.get("/api/users", headers=_auth(editor_token))
        assert resp.status_code == 403

    async def test_viewer_cannot_create_users(self, router_client: AsyncClient) -> None:
        admin_token = await _bootstrap_admin(router_client)
        await _create_user(router_client, admin_token, "viewer@example.com", role="viewer")
        viewer_token = await _login(router_client, "viewer@example.com", "password-12345")

        resp = await router_client.post(
            "/api/users",
            headers=_auth(viewer_token),
            json={
                "email": "new@example.com",
                "password": "password-12345",
                "role": "editor",
            },
        )
        assert resp.status_code == 403

    async def test_deactivated_admin_loses_access_with_existing_jwt(
        self, router_client: AsyncClient
    ) -> None:
        """``get_current_user`` re-reads ``is_active`` from the DB on every call,
        so a deactivated admin's still-unexpired JWT must be refused."""
        admin_token = await _bootstrap_admin(router_client)
        second = await _create_user(
            router_client, admin_token, "second@example.com", role="admin"
        )
        second_token = await _login(router_client, "second@example.com", "password-12345")

        # Second admin deactivates the bootstrap admin.
        me = await router_client.get("/api/auth/me", headers=_auth(admin_token))
        bootstrap_id = me.json()["id"]
        resp = await router_client.patch(
            f"/api/users/{bootstrap_id}",
            headers=_auth(second_token),
            json={"is_active": False},
        )
        assert resp.status_code == 200

        # Bootstrap admin's old token is now useless against any protected endpoint.
        resp = await router_client.get("/api/users", headers=_auth(admin_token))
        assert resp.status_code == 401

        # Second admin is unaffected.
        assert second["role"] == "admin"
        resp = await router_client.get("/api/users", headers=_auth(second_token))
        assert resp.status_code == 200


class TestUserCrud:
    async def test_list_returns_all_users(self, router_client: AsyncClient) -> None:
        admin_token = await _bootstrap_admin(router_client)
        await _create_user(router_client, admin_token, "a@example.com")
        await _create_user(router_client, admin_token, "b@example.com", role="viewer")

        resp = await router_client.get("/api/users", headers=_auth(admin_token))
        assert resp.status_code == 200
        emails = sorted(u["email"] for u in resp.json())
        assert emails == ["a@example.com", "b@example.com", "root@example.com"]

    async def test_create_user_rejects_short_password(
        self, router_client: AsyncClient
    ) -> None:
        admin_token = await _bootstrap_admin(router_client)
        resp = await router_client.post(
            "/api/users",
            headers=_auth(admin_token),
            json={
                "email": "x@example.com",
                "password": "short",
                "role": "editor",
            },
        )
        assert resp.status_code == 400

    async def test_create_user_rejects_duplicate_email(
        self, router_client: AsyncClient
    ) -> None:
        admin_token = await _bootstrap_admin(router_client)
        await _create_user(router_client, admin_token, "dup@example.com")
        resp = await router_client.post(
            "/api/users",
            headers=_auth(admin_token),
            json={
                "email": "dup@example.com",
                "password": "password-12345",
                "role": "editor",
            },
        )
        assert resp.status_code == 409

    async def test_create_rejects_invalid_role(self, router_client: AsyncClient) -> None:
        admin_token = await _bootstrap_admin(router_client)
        resp = await router_client.post(
            "/api/users",
            headers=_auth(admin_token),
            json={
                "email": "bad@example.com",
                "password": "password-12345",
                "role": "superuser",
            },
        )
        assert resp.status_code == 422

    async def test_get_user_returns_detail(self, router_client: AsyncClient) -> None:
        admin_token = await _bootstrap_admin(router_client)
        created = await _create_user(router_client, admin_token, "detail@example.com")

        resp = await router_client.get(
            f"/api/users/{created['id']}", headers=_auth(admin_token)
        )
        assert resp.status_code == 200
        assert resp.json()["email"] == "detail@example.com"

    async def test_get_unknown_user_returns_404(self, router_client: AsyncClient) -> None:
        admin_token = await _bootstrap_admin(router_client)
        resp = await router_client.get("/api/users/9999", headers=_auth(admin_token))
        assert resp.status_code == 404

    async def test_update_user_email_and_role(self, router_client: AsyncClient) -> None:
        admin_token = await _bootstrap_admin(router_client)
        created = await _create_user(router_client, admin_token, "edit@example.com")

        resp = await router_client.patch(
            f"/api/users/{created['id']}",
            headers=_auth(admin_token),
            json={"email": "renamed@example.com", "role": "viewer"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["email"] == "renamed@example.com"
        assert body["role"] == "viewer"

    async def test_update_rejects_duplicate_email(self, router_client: AsyncClient) -> None:
        admin_token = await _bootstrap_admin(router_client)
        await _create_user(router_client, admin_token, "first@example.com")
        second = await _create_user(router_client, admin_token, "second@example.com")

        resp = await router_client.patch(
            f"/api/users/{second['id']}",
            headers=_auth(admin_token),
            json={"email": "first@example.com"},
        )
        assert resp.status_code == 409

    async def test_admin_reset_password_invalidates_old_password(
        self, router_client: AsyncClient
    ) -> None:
        admin_token = await _bootstrap_admin(router_client)
        created = await _create_user(
            router_client, admin_token, "reset@example.com", password="oldpassword1"
        )

        resp = await router_client.post(
            f"/api/users/{created['id']}/reset-password",
            headers=_auth(admin_token),
            json={"new_password": "newpassword9"},
        )
        assert resp.status_code == 200

        old = await router_client.post(
            "/api/auth/login",
            json={"email": "reset@example.com", "password": "oldpassword1"},
        )
        assert old.status_code == 401

        new = await router_client.post(
            "/api/auth/login",
            json={"email": "reset@example.com", "password": "newpassword9"},
        )
        assert new.status_code == 200

    async def test_reset_password_rejects_short_password(
        self, router_client: AsyncClient
    ) -> None:
        admin_token = await _bootstrap_admin(router_client)
        created = await _create_user(router_client, admin_token, "reset@example.com")
        resp = await router_client.post(
            f"/api/users/{created['id']}/reset-password",
            headers=_auth(admin_token),
            json={"new_password": "short"},
        )
        assert resp.status_code == 400

    async def test_delete_user_happy_path(self, router_client: AsyncClient) -> None:
        admin_token = await _bootstrap_admin(router_client)
        created = await _create_user(router_client, admin_token, "doomed@example.com")

        resp = await router_client.delete(
            f"/api/users/{created['id']}", headers=_auth(admin_token)
        )
        assert resp.status_code == 204

        follow = await router_client.get(
            f"/api/users/{created['id']}", headers=_auth(admin_token)
        )
        assert follow.status_code == 404


class TestSelfProtection:
    async def test_cannot_delete_self(self, router_client: AsyncClient) -> None:
        admin_token = await _bootstrap_admin(router_client)
        me = await router_client.get("/api/auth/me", headers=_auth(admin_token))
        my_id = me.json()["id"]

        resp = await router_client.delete(
            f"/api/users/{my_id}", headers=_auth(admin_token)
        )
        assert resp.status_code == 409

    async def test_cannot_deactivate_self(self, router_client: AsyncClient) -> None:
        admin_token = await _bootstrap_admin(router_client)
        await _create_user(
            router_client, admin_token, "co-admin@example.com", role="admin"
        )
        me = await router_client.get("/api/auth/me", headers=_auth(admin_token))
        my_id = me.json()["id"]

        resp = await router_client.patch(
            f"/api/users/{my_id}",
            headers=_auth(admin_token),
            json={"is_active": False},
        )
        assert resp.status_code == 409
        assert "own account" in resp.json()["detail"]

    async def test_cannot_change_own_role(self, router_client: AsyncClient) -> None:
        admin_token = await _bootstrap_admin(router_client)
        await _create_user(
            router_client, admin_token, "co-admin@example.com", role="admin"
        )
        me = await router_client.get("/api/auth/me", headers=_auth(admin_token))
        my_id = me.json()["id"]

        resp = await router_client.patch(
            f"/api/users/{my_id}",
            headers=_auth(admin_token),
            json={"role": "editor"},
        )
        assert resp.status_code == 409


class TestLastAdminHelper:
    """Direct coverage of the last-admin invariant.

    The 409 branches in update/delete are genuinely unreachable via the
    public API (reaching them requires the caller to be an active admin,
    which guarantees at least one admin survives). These tests drive the
    helper against a real session so the defense-in-depth guard is still
    exercised.
    """

    async def test_count_active_admins_excludes_inactive_and_non_admins(
        self, tmp_path: Path
    ) -> None:
        from septum_api.routers.users import _count_active_admins
        from septum_api.services.auth import hash_password

        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'helper.db'}", echo=False
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)

        async with session_maker() as session:
            session.add_all(
                [
                    User(
                        email="a@example.com",
                        hashed_password=hash_password("password-12345"),
                        role="admin",
                        is_active=True,
                    ),
                    User(
                        email="b@example.com",
                        hashed_password=hash_password("password-12345"),
                        role="admin",
                        is_active=False,
                    ),
                    User(
                        email="c@example.com",
                        hashed_password=hash_password("password-12345"),
                        role="editor",
                        is_active=True,
                    ),
                ]
            )
            await session.commit()

            assert await _count_active_admins(session) == 1

            row = await session.execute(select(User).where(User.email == "a@example.com"))
            only_admin = row.scalar_one()
            assert await _count_active_admins(session, exclude_id=only_admin.id) == 0

        await engine.dispose()
