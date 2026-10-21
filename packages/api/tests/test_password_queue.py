"""The bcrypt queue: bounded lanes, one job per identity, fast 503 past the cap."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest
from httpx import AsyncClient
from septum_api.services import auth as auth_service


@pytest.fixture
def pool(monkeypatch: pytest.MonkeyPatch):
    """Two workers (account lane: 2 slots, public lane: 16 - 2 = 14); a gate holds jobs."""
    monkeypatch.setattr(auth_service, "_password_workers", lambda: 2)
    auth_service.shutdown_password_executor()
    gate = threading.Event()
    yield gate
    gate.set()
    auth_service.shutdown_password_executor()


async def _settle() -> None:
    await asyncio.sleep(0.05)


def _pending() -> tuple[int, int, int]:
    return auth_service._pending_public, auth_service._pending_account, len(auth_service._in_flight)


def test_capacity_floor_and_per_worker_share(monkeypatch):
    monkeypatch.setattr(auth_service, "_password_workers", lambda: 2)
    assert auth_service.password_queue_capacity() == 16
    monkeypatch.setattr(auth_service, "_password_workers", lambda: 7)
    assert auth_service.password_queue_capacity() == 56
    assert auth_service.account_queue_capacity() == 7


async def test_one_job_in_flight_per_identity(pool):
    held = asyncio.create_task(
        auth_service.run_password_work(pool.wait, lane="public", identity="ip:a")
    )
    await _settle()
    with pytest.raises(auth_service.PasswordWorkBusy):
        await auth_service.run_password_work(lambda: None, lane="public", identity="ip:a")
    other = asyncio.create_task(
        auth_service.run_password_work(lambda: "b", lane="public", identity="ip:b")
    )
    pool.set()
    await held
    assert await other == "b"
    await _settle()
    assert _pending() == (0, 0, 0)


async def test_account_work_cannot_take_the_public_lane(pool):
    held = [
        asyncio.create_task(
            auth_service.run_password_work(pool.wait, lane="account", identity=f"user:{i}")
        )
        for i in range(2)
    ]
    await _settle()
    with pytest.raises(auth_service.PasswordWorkBusy):
        await auth_service.run_password_work(lambda: None, lane="account", identity="user:9")
    login = asyncio.create_task(
        auth_service.run_password_work(lambda: "login", lane="public", identity="ip:x")
    )
    await _settle()
    assert _pending()[0] == 1
    pool.set()
    await asyncio.gather(*held)
    assert await login == "login"


async def test_admin_work_waits_for_an_account_slot(pool):
    held = [
        asyncio.create_task(auth_service.run_password_work(pool.wait, lane="account", identity=f"user:{i}"))
        for i in range(2)
    ]
    await _settle()
    admin = asyncio.create_task(auth_service.run_password_work(lambda: "admin"))
    await _settle()
    assert not admin.done()
    pool.set()
    await asyncio.gather(*held)
    assert await admin == "admin"


async def test_public_lane_refuses_when_full(pool, monkeypatch):
    monkeypatch.setattr(auth_service, "password_queue_capacity", lambda: 3)
    held = [
        asyncio.create_task(auth_service.run_password_work(pool.wait, lane="public", identity=f"ip:{i}"))
        for i in range(1)
    ]
    await _settle()
    with pytest.raises(auth_service.PasswordWorkBusy):
        await auth_service.run_password_work(lambda: None, lane="public", identity="ip:z")
    pool.set()
    await asyncio.gather(*held)


async def test_cancelled_waiter_releases_its_slot_and_its_job_never_runs(monkeypatch):
    monkeypatch.setattr(auth_service, "_password_workers", lambda: 1)
    auth_service.shutdown_password_executor()
    gate = threading.Event()
    ran: list[str] = []
    try:
        blocker = asyncio.create_task(
            auth_service.run_password_work(gate.wait, lane="public", identity="ip:1")
        )
        await _settle()
        waiter = asyncio.create_task(
            auth_service.run_password_work(lambda: ran.append("x"), lane="public", identity="ip:2")
        )
        await _settle()
        assert _pending() == (2, 0, 2)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        await _settle()
        assert _pending() == (1, 0, 1)
        gate.set()
        await blocker
        await _settle()
        assert ran == []
        assert _pending() == (0, 0, 0)
    finally:
        gate.set()
        auth_service.shutdown_password_executor()


async def _register(client: AsyncClient) -> str:
    resp = await client.post(
        "/api/auth/register", json={"email": "root@example.com", "password": "password-12345"}
    )
    assert resp.status_code == 201
    return resp.json()["access_token"]


@pytest.mark.parametrize("email", ["root@example.com", "nobody@example.com"])
async def test_login_answers_fast_503_when_the_public_lane_is_full(router_client, monkeypatch, email):
    await _register(router_client)
    monkeypatch.setattr(auth_service, "password_queue_capacity", lambda: 0)
    resp = await router_client.post("/api/auth/login", json={"email": email, "password": "password-12345"})
    assert resp.status_code == 503
    assert resp.json()["code"] == "server_busy"
    assert resp.headers["Retry-After"] == str(auth_service.PASSWORD_BUSY_RETRY_AFTER_SECONDS)


async def test_change_password_flood_cannot_block_a_login(router_client, monkeypatch):
    token = await _register(router_client)
    monkeypatch.setattr(auth_service, "_password_workers", lambda: 2)
    auth_service.shutdown_password_executor()
    gate = threading.Event()
    real_verify = auth_service.verify_password

    def slow_verify(plain, hashed):
        gate.wait(5)
        return real_verify(plain, hashed)

    from septum_api.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "verify_password", slow_verify)
    headers = {"Authorization": f"Bearer {token}"}
    body = {"current_password": "password-12345", "new_password": "password-67890-new"}
    try:
        flood = [
            asyncio.create_task(router_client.post("/api/auth/change-password", json=body, headers=headers))
            for _ in range(4)
        ]
        await _settle()
        login = await router_client.post(
            "/api/auth/login", json={"email": "root@example.com", "password": "password-12345"}
        )
        gate.set()
        codes = sorted([(await t).status_code for t in flood])
    finally:
        gate.set()
        auth_service.shutdown_password_executor()
    assert login.status_code == 200
    assert codes.count(503) == 3


def _cgroup(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, content in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return tmp_path


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"cpu.max": "200000 100000\n"}, 2),
        ({"cpu.max": "150000 100000\n"}, 2),
        ({"cpu.max": "max 100000\n"}, None),
        ({"cpu/cpu.cfs_quota_us": "300000\n", "cpu/cpu.cfs_period_us": "100000\n"}, 3),
        ({"cpu/cpu.cfs_quota_us": "-1\n", "cpu/cpu.cfs_period_us": "100000\n"}, None),
        ({}, None),
    ],
)
def test_available_cpus_respects_the_cgroup_quota(tmp_path, monkeypatch, files, expected):
    monkeypatch.setattr(auth_service.os, "process_cpu_count", lambda: 64)
    root = _cgroup(tmp_path, files)
    assert auth_service.available_cpus(root) == (expected or 64)


def test_malformed_cgroup_file_falls_back_and_is_logged_once(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(auth_service.os, "process_cpu_count", lambda: 8)
    monkeypatch.setattr(auth_service, "_cgroup_warning_logged", False)
    root = _cgroup(tmp_path, {"cpu.max": "garbage\n"})
    with caplog.at_level("WARNING"):
        assert auth_service.available_cpus(root) == 8
        assert auth_service.available_cpus(root) == 8
    assert caplog.text.count("Ignoring unreadable cgroup CPU quota: ValueError") == 1


async def test_waiting_admins_are_served_in_order_before_new_account_work(pool):
    gates = [threading.Event(), threading.Event()]
    held = [
        asyncio.create_task(
            auth_service.run_password_work(gates[i].wait, lane="account", identity=f"user:{i}")
        )
        for i in range(2)
    ]
    await _settle()
    order: list[str] = []

    def admin_job(name: str):
        order.append(name)
        pool.wait(5)

    first = asyncio.create_task(auth_service.run_password_work(admin_job, "admin-1"))
    await _settle()
    second = asyncio.create_task(auth_service.run_password_work(admin_job, "admin-2"))
    await _settle()

    gates[0].set()
    await held[0]
    await _settle()
    # The freed slot went to the oldest waiter, not to new account work or the later admin.
    assert order == ["admin-1"]
    with pytest.raises(auth_service.PasswordWorkBusy):
        await auth_service.run_password_work(lambda: None, lane="account", identity="user:new")

    gates[1].set()
    pool.set()
    await asyncio.gather(held[1], first, second)
    assert order == ["admin-1", "admin-2"]
    await _settle()
    assert _pending() == (0, 0, 0)
    assert not auth_service._account_waiters


async def test_cancelled_admin_wait_gives_back_its_place(pool):
    held = [
        asyncio.create_task(auth_service.run_password_work(pool.wait, lane="account", identity=f"user:{i}"))
        for i in range(2)
    ]
    await _settle()
    admin = asyncio.create_task(auth_service.run_password_work(lambda: "never"))
    await _settle()
    admin.cancel()
    with pytest.raises(asyncio.CancelledError):
        await admin
    assert not auth_service._account_waiters
    pool.set()
    await asyncio.gather(*held)
    await _settle()
    assert _pending() == (0, 0, 0)


def test_cpu_count_without_process_cpu_count(monkeypatch):
    monkeypatch.delattr(auth_service.os, "process_cpu_count", raising=False)
    monkeypatch.setattr(auth_service.os, "sched_getaffinity", lambda pid: {0, 1, 2}, raising=False)
    assert auth_service._process_cpus() == 3
    monkeypatch.delattr(auth_service.os, "sched_getaffinity", raising=False)
    monkeypatch.setattr(auth_service.os, "cpu_count", lambda: 5)
    assert auth_service._process_cpus() == 5
