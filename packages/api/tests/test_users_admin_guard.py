"""Last-admin guard under concurrent writes, and the PATCH email-collision contract.

The SQLite tests commit a rival change from a second connection right before
the request's own write to ``users``, the worst interleaving two admins
editing each other can produce. The PostgreSQL test runs real concurrent
transactions when ``SEPTUM_TEST_POSTGRES_URL`` is set and is skipped otherwise.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from septum_api.models import Base
from septum_api.models.user import User
from septum_api.routers import users as users_router
from septum_api.routers.users import UserUpdateRequest, delete_user, update_user
from sqlalchemy import event, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

POSTGRES_URL = os.getenv("SEPTUM_TEST_POSTGRES_URL", "")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _bootstrap_admin(client: AsyncClient) -> str:
    resp = await client.post(
        "/api/auth/register",
        json={"email": "root@example.com", "password": "password-12345"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


async def _create_user(
    client: AsyncClient, admin_token: str, email: str, role: str = "editor"
) -> dict:
    resp = await client.post(
        "/api/users",
        headers=_auth(admin_token),
        json={"email": email, "password": "password-12345", "role": role},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _login(client: AsyncClient, email: str) -> str:
    resp = await client.post(
        "/api/auth/login", json={"email": email, "password": "password-12345"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _active_admin_count(db_path: Path) -> int:
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT count(*) FROM users WHERE role = 'admin' AND is_active = 1"
        ).fetchone()[0]


@pytest.fixture
def rival_write_before_own(monkeypatch: pytest.MonkeyPatch):
    """Return an installer that commits ``statements`` from another connection
    just before the request's first ``UPDATE users`` / ``DELETE FROM users``."""
    installed: list = []

    def install(db_path: Path, *statements: tuple[str, tuple]) -> None:
        fired: list[bool] = []

        def before(conn, cursor, statement, parameters, context, executemany):
            head = statement.lstrip().upper()
            if fired or not head.startswith(("UPDATE USERS", "DELETE FROM USERS")):
                return
            fired.append(True)
            with sqlite3.connect(db_path, timeout=1) as rival:
                for sql, params in statements:
                    rival.execute(sql, params)

        event.listen(Engine, "before_cursor_execute", before)
        installed.append(before)

    yield install
    for listener in installed:
        event.remove(Engine, "before_cursor_execute", listener)


class TestLastAdminRaceOnSqlite:
    async def test_demotion_racing_a_committed_deactivation_is_refused(
        self, router_client: AsyncClient, tmp_path: Path, rival_write_before_own
    ) -> None:
        root_token = await _bootstrap_admin(router_client)
        other = await _create_user(router_client, root_token, "b@example.com", role="admin")
        other_token = await _login(router_client, "b@example.com")
        root_id = (await router_client.get("/api/auth/me", headers=_auth(root_token))).json()["id"]
        db_path = tmp_path / "router_test.db"
        rival_write_before_own(db_path, ("UPDATE users SET is_active = 0 WHERE id = ?", (other["id"],)))

        resp = await router_client.patch(
            f"/api/users/{root_id}", headers=_auth(other_token), json={"role": "editor"}
        )

        assert resp.status_code == 409
        assert resp.json()["detail"] == "Cannot demote the last active administrator"
        assert _active_admin_count(db_path) == 1

    async def test_deactivation_racing_a_committed_demotion_is_refused(
        self, router_client: AsyncClient, tmp_path: Path, rival_write_before_own
    ) -> None:
        root_token = await _bootstrap_admin(router_client)
        other = await _create_user(router_client, root_token, "b@example.com", role="admin")
        other_token = await _login(router_client, "b@example.com")
        root_id = (await router_client.get("/api/auth/me", headers=_auth(root_token))).json()["id"]
        db_path = tmp_path / "router_test.db"
        rival_write_before_own(db_path, ("UPDATE users SET role = 'viewer' WHERE id = ?", (other["id"],)))

        resp = await router_client.patch(
            f"/api/users/{root_id}", headers=_auth(other_token), json={"is_active": False}
        )

        assert resp.status_code == 409
        assert resp.json()["detail"] == "Cannot deactivate the last active administrator"
        assert _active_admin_count(db_path) == 1

    async def test_delete_racing_a_committed_deactivation_is_refused(
        self, router_client: AsyncClient, tmp_path: Path, rival_write_before_own
    ) -> None:
        root_token = await _bootstrap_admin(router_client)
        other = await _create_user(router_client, root_token, "b@example.com", role="admin")
        other_token = await _login(router_client, "b@example.com")
        root_id = (await router_client.get("/api/auth/me", headers=_auth(root_token))).json()["id"]
        db_path = tmp_path / "router_test.db"
        rival_write_before_own(db_path, ("UPDATE users SET is_active = 0 WHERE id = ?", (other["id"],)))

        resp = await router_client.delete(f"/api/users/{root_id}", headers=_auth(other_token))

        assert resp.status_code == 409
        assert resp.json()["detail"] == "Cannot delete the last active administrator"
        assert _active_admin_count(db_path) == 1
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT count(*) FROM users WHERE id = ?", (root_id,)).fetchone()[0] == 1

    async def test_demotion_without_a_rival_still_succeeds(
        self, router_client: AsyncClient, tmp_path: Path
    ) -> None:
        root_token = await _bootstrap_admin(router_client)
        other = await _create_user(router_client, root_token, "b@example.com", role="admin")

        resp = await router_client.patch(
            f"/api/users/{other['id']}", headers=_auth(root_token), json={"role": "editor"}
        )

        assert resp.status_code == 200
        assert _active_admin_count(tmp_path / "router_test.db") == 1


    async def test_demotion_decided_on_a_stale_non_admin_read_is_refused(
        self, router_client: AsyncClient, tmp_path: Path, rival_write_before_own
    ) -> None:
        """The target was an editor when read; before the write it became the only admin."""
        root_token = await _bootstrap_admin(router_client)
        root_id = (await router_client.get("/api/auth/me", headers=_auth(root_token))).json()["id"]
        target = await _create_user(router_client, root_token, "t@example.com")
        db_path = tmp_path / "router_test.db"
        rival_write_before_own(
            db_path,
            ("UPDATE users SET role = 'admin' WHERE id = ?", (target["id"],)),
            ("UPDATE users SET role = 'editor' WHERE id = ?", (root_id,)),
        )

        resp = await router_client.patch(
            f"/api/users/{target['id']}", headers=_auth(root_token), json={"role": "viewer"}
        )

        assert resp.status_code == 409
        assert resp.json()["detail"] == "Cannot demote the last active administrator"
        assert _active_admin_count(db_path) == 1

    async def test_deactivation_decided_on_a_stale_non_admin_read_is_refused(
        self, router_client: AsyncClient, tmp_path: Path, rival_write_before_own
    ) -> None:
        root_token = await _bootstrap_admin(router_client)
        root_id = (await router_client.get("/api/auth/me", headers=_auth(root_token))).json()["id"]
        target = await _create_user(router_client, root_token, "t@example.com")
        db_path = tmp_path / "router_test.db"
        rival_write_before_own(
            db_path,
            ("UPDATE users SET role = 'admin' WHERE id = ?", (target["id"],)),
            ("UPDATE users SET role = 'editor' WHERE id = ?", (root_id,)),
        )

        resp = await router_client.patch(
            f"/api/users/{target['id']}", headers=_auth(root_token), json={"is_active": False}
        )

        assert resp.status_code == 409
        assert resp.json()["detail"] == "Cannot deactivate the last active administrator"
        assert _active_admin_count(db_path) == 1


def _install_trigger(db_path: Path, sql: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(sql)


_ABORT_USER_UPDATES = (
    "CREATE TRIGGER abort_user_updates BEFORE UPDATE ON users "
    "BEGIN SELECT RAISE(ABORT, 'blocked by test trigger'); END"
)


class TestOtherIntegrityErrors:
    """Only the email uniqueness collision becomes 409; other constraint failures propagate."""

    async def test_update_with_a_free_email_propagates_a_non_email_failure(
        self, router_client: AsyncClient, tmp_path: Path
    ) -> None:
        root_token = await _bootstrap_admin(router_client)
        target = await _create_user(router_client, root_token, "free@example.com")
        _install_trigger(tmp_path / "router_test.db", _ABORT_USER_UPDATES)

        with pytest.raises(IntegrityError):
            await router_client.patch(
                f"/api/users/{target['id']}",
                headers=_auth(root_token),
                json={"email": "still-free@example.com"},
            )

    async def test_update_without_an_email_change_propagates_a_failure(
        self, router_client: AsyncClient, tmp_path: Path
    ) -> None:
        root_token = await _bootstrap_admin(router_client)
        target = await _create_user(router_client, root_token, "e@example.com")
        _install_trigger(tmp_path / "router_test.db", _ABORT_USER_UPDATES)

        with pytest.raises(IntegrityError):
            await router_client.patch(
                f"/api/users/{target['id']}", headers=_auth(root_token), json={"role": "admin"}
            )

    async def test_create_propagates_a_non_email_failure(
        self, router_client: AsyncClient, tmp_path: Path
    ) -> None:
        root_token = await _bootstrap_admin(router_client)
        _install_trigger(
            tmp_path / "router_test.db",
            "CREATE TRIGGER abort_user_inserts BEFORE INSERT ON users "
            "BEGIN SELECT RAISE(ABORT, 'blocked by test trigger'); END",
        )

        with pytest.raises(IntegrityError):
            await router_client.post(
                "/api/users",
                headers=_auth(root_token),
                json={"email": "new@example.com", "password": "password-12345"},
            )


class TestExplicitNullUpdate:
    @pytest.mark.parametrize("field", ["email", "role", "is_active"])
    async def test_explicit_null_is_422_and_locks_nothing(
        self, router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch, field: str
    ) -> None:
        root_token = await _bootstrap_admin(router_client)
        target = await _create_user(router_client, root_token, "b@example.com", role="admin")
        lock = AsyncMock(side_effect=AssertionError("admin rows must not be locked"))
        monkeypatch.setattr(users_router, "_lock_admin_rows", lock)

        resp = await router_client.patch(
            f"/api/users/{target['id']}", headers=_auth(root_token), json={field: None}
        )

        assert resp.status_code == 422
        lock.assert_not_awaited()

    async def test_omitted_fields_still_mean_unchanged(self, router_client: AsyncClient) -> None:
        root_token = await _bootstrap_admin(router_client)
        target = await _create_user(router_client, root_token, "b@example.com", role="viewer")

        resp = await router_client.patch(
            f"/api/users/{target['id']}", headers=_auth(root_token), json={}
        )

        assert resp.status_code == 200
        assert resp.json()["role"] == "viewer"


class TestEmailCollisionAtCommit:
    async def test_collision_missed_by_the_precheck_is_409_not_500(
        self, router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root_token = await _bootstrap_admin(router_client)
        await _create_user(router_client, root_token, "taken@example.com")
        target = await _create_user(router_client, root_token, "free@example.com")
        # Stands in for a concurrent request taking the email after the check.
        monkeypatch.setattr(users_router, "_refuse_taken_email", AsyncMock(return_value=None))

        resp = await router_client.patch(
            f"/api/users/{target['id']}",
            headers=_auth(root_token),
            json={"email": "taken@example.com"},
        )

        assert resp.status_code == 409
        assert resp.json()["detail"] == "Email already registered"
        listed = await router_client.get("/api/users", headers=_auth(root_token))
        emails = sorted(u["email"] for u in listed.json())
        assert emails == ["free@example.com", "root@example.com", "taken@example.com"]

    async def test_collision_with_an_admin_loss_check_is_409(
        self, router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root_token = await _bootstrap_admin(router_client)
        await _create_user(router_client, root_token, "taken@example.com")
        target = await _create_user(router_client, root_token, "b@example.com", role="admin")
        monkeypatch.setattr(users_router, "_refuse_taken_email", AsyncMock(return_value=None))

        resp = await router_client.patch(
            f"/api/users/{target['id']}",
            headers=_auth(root_token),
            json={"email": "taken@example.com", "role": "editor"},
        )

        assert resp.status_code == 409
        assert resp.json()["detail"] == "Email already registered"


@pytest.fixture
async def postgres_session_maker():
    """Yield a READ COMMITTED session maker bound to a throwaway schema."""
    schema = f"admin_guard_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(POSTGRES_URL)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        POSTGRES_URL,
        isolation_level="READ COMMITTED",
        connect_args={"server_settings": {"search_path": schema}},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def _seed_two_admins(session_maker) -> list[int]:
    async with session_maker() as db:
        db.add_all([
            User(email="a@example.com", hashed_password="h", role="admin"),
            User(email="b@example.com", hashed_password="h", role="admin"),
        ])
        await db.commit()
        return list((await db.scalars(select(User.id).order_by(User.id))).all())


def _lock_only_after_both_arrive(monkeypatch: pytest.MonkeyPatch) -> None:
    both_arrived = asyncio.Barrier(2)
    real_lock = users_router._lock_admin_rows

    async def lock_after_both_arrive(db, target_id):
        await both_arrived.wait()
        return await real_lock(db, target_id)

    monkeypatch.setattr(users_router, "_lock_admin_rows", lock_after_both_arrive)


async def _count(session_maker, *conditions) -> int:
    async with session_maker() as db:
        return await db.scalar(select(func.count()).select_from(User).where(*conditions))


async def _active_admins(session_maker) -> int:
    return await _count(session_maker, User.role == "admin", User.is_active.is_(True))


async def _status_of(call) -> int:
    try:
        await call
    except HTTPException as exc:
        return exc.status_code
    return 200


@pytest.mark.skipif(not POSTGRES_URL, reason="SEPTUM_TEST_POSTGRES_URL not set")
@pytest.mark.parametrize("body", [{"role": "editor"}, {"is_active": False}])
async def test_two_admins_removing_each_other_on_postgres_leave_one(
    postgres_session_maker, monkeypatch: pytest.MonkeyPatch, body: dict
) -> None:
    ids = await _seed_two_admins(postgres_session_maker)
    _lock_only_after_both_arrive(monkeypatch)

    async def remove(actor_id: int, target_id: int) -> int:
        async with postgres_session_maker() as db:
            actor = await db.get(User, actor_id)
            return await _status_of(update_user(target_id, UserUpdateRequest(**body), actor, db))

    codes = await asyncio.gather(remove(ids[0], ids[1]), remove(ids[1], ids[0]))

    assert sorted(codes) == [200, 409]
    assert await _active_admins(postgres_session_maker) == 1


@pytest.mark.skipif(not POSTGRES_URL, reason="SEPTUM_TEST_POSTGRES_URL not set")
async def test_two_admins_deleting_each_other_on_postgres_leave_one(
    postgres_session_maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = await _seed_two_admins(postgres_session_maker)
    _lock_only_after_both_arrive(monkeypatch)

    async def delete(actor_id: int, target_id: int) -> int:
        async with postgres_session_maker() as db:
            actor = await db.get(User, actor_id)
            return await _status_of(delete_user(target_id, actor, db))

    codes = await asyncio.gather(delete(ids[0], ids[1]), delete(ids[1], ids[0]))

    assert sorted(codes) == [200, 409]
    assert await _active_admins(postgres_session_maker) == 1
    assert await _count(postgres_session_maker) == 1
