"""API keys must not reach account or credential management routes."""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from septum_api import database
from septum_api.database import get_db
from septum_api.main import app
from septum_api.middleware import auth as auth_middleware
from septum_api.models import Base
from septum_api.services.api_key_service import HEADER_NAME, generate_api_key
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ADMIN_EMAIL = "root@example.com"
ADMIN_PASSWORD = "password-12345"


@pytest.fixture
async def key_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[AsyncClient, str, str]]:
    """Client whose AuthMiddleware resolves API keys against a per-test DB.

    Yields ``(client, admin_jwt, admin_api_key)``.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'keys.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _override_get_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.setattr(database, "engine_is_ready", lambda: True)
    monkeypatch.setattr(database, "get_session_maker", lambda: session_maker)
    monkeypatch.setenv("SEPTUM_SETUP_TOKEN", "credential-guard-setup-token-0123456789")
    limiter = getattr(app.state, "limiter", None)
    if limiter is not None:
        limiter.enabled = False
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/auth/register",
                json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                headers={"X-Setup-Token": "credential-guard-setup-token-0123456789"},
            )
            assert resp.status_code == 201, resp.text
            jwt_token = resp.json()["access_token"]
            me = await client.get(
                "/api/auth/me", headers={"Authorization": f"Bearer {jwt_token}"}
            )
            async with session_maker() as db:
                raw_key, _ = await generate_api_key(
                    db, user_id=me.json()["id"], name="automation"
                )
            yield client, jwt_token, raw_key
    finally:
        if limiter is not None:
            limiter.enabled = True
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


BLOCKED_ROUTES = [
    ("POST", "/api/api-keys", {"name": "escalated"}),
    ("GET", "/api/api-keys", None),
    ("DELETE", "/api/api-keys/1", None),
    ("GET", "/api/users", None),
    ("POST", "/api/users", {"email": "x@example.com", "password": ADMIN_PASSWORD, "role": "admin"}),
    ("GET", "/api/users/1", None),
    ("PATCH", "/api/users/1", {"role": "viewer"}),
    ("POST", "/api/users/1/reset-password", {"new_password": "brand-new-password"}),
    ("DELETE", "/api/users/1", None),
    ("POST", "/api/auth/change-password", {"current_password": ADMIN_PASSWORD, "new_password": "brand-new-password"}),
    ("PATCH", "/api/settings", {"openai_api_key": "sk-attacker"}),
]

POLICY_WRITE_ROUTES = [
    ("PATCH", "/api/regulations/1/activate", {"is_active": False}),
    ("POST", "/api/regulations/custom", {"name": "x", "entity_type": "X", "detection_method": "regex", "pattern": "x"}),
    ("PATCH", "/api/regulations/custom/1", {"is_active": False}),
    ("DELETE", "/api/regulations/custom/1", None),
    ("POST", "/api/regulations/non-pii", {"pattern": "x"}),
    ("PATCH", "/api/regulations/non-pii/1", {"is_active": False}),
    ("DELETE", "/api/regulations/non-pii/1", None),
    ("POST", "/api/text-normalization", {"name": "x", "pattern": "x", "replacement": "y"}),
    ("PATCH", "/api/text-normalization/1", {"is_active": False}),
    ("DELETE", "/api/text-normalization/1", None),
]

POLICY_READ_ROUTES = [
    "/api/regulations",
    "/api/regulations/custom",
    "/api/regulations/non-pii",
    "/api/text-normalization",
]


@pytest.mark.parametrize("method,path,body", BLOCKED_ROUTES + POLICY_WRITE_ROUTES)
async def test_api_key_is_rejected_on_credential_routes(
    key_client: tuple[AsyncClient, str, str], method: str, path: str, body: dict | None
) -> None:
    client, _jwt, raw_key = key_client
    resp = await client.request(method, path, headers={HEADER_NAME: raw_key}, json=body)
    assert resp.status_code == 403, resp.text
    assert "interactive" in resp.json()["detail"]


async def test_jwt_still_manages_keys_and_users(
    key_client: tuple[AsyncClient, str, str],
) -> None:
    client, jwt_token, _raw_key = key_client
    headers = {"Authorization": f"Bearer {jwt_token}"}
    assert (await client.get("/api/api-keys", headers=headers)).status_code == 200
    assert (await client.post("/api/api-keys", headers=headers, json={"name": "ci"})).status_code == 201
    assert (await client.get("/api/users", headers=headers)).status_code == 200
    assert (await client.patch("/api/settings", headers=headers, json={})).status_code == 200


async def test_api_key_still_works_on_data_routes(
    key_client: tuple[AsyncClient, str, str],
) -> None:
    client, _jwt, raw_key = key_client
    headers = {HEADER_NAME: raw_key}
    me = await client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["email"] == ADMIN_EMAIL
    regulations = await client.get("/api/regulations", headers=headers)
    assert regulations.status_code == 200, regulations.text
    settings = await client.get("/api/settings", headers=headers)
    assert settings.status_code == 200, settings.text


POLICY_LIFECYCLES = [
    (
        "/api/regulations/custom",
        {"name": "ticket", "entity_type": "TICKET_ID", "detection_method": "regex",
         "pattern": "TCK-[0-9]{6}", "placeholder_label": "TICKET"},
        {"is_active": False},
    ),
    (
        "/api/regulations/non-pii",
        {"pattern_type": "token", "pattern": "Invoice"},
        {"is_active": False},
    ),
    (
        "/api/text-normalization",
        {"name": "dash", "pattern": "\u2013", "replacement": "-"},
        {"name": "dash", "pattern": "\u2013", "replacement": "-", "is_active": False},
    ),
]


@pytest.mark.parametrize("base,create_body,update_body", POLICY_LIFECYCLES)
async def test_jwt_can_create_update_delete_policy_rules(
    key_client: tuple[AsyncClient, str, str],
    base: str,
    create_body: dict,
    update_body: dict,
) -> None:
    client, jwt_token, _raw_key = key_client
    headers = {"Authorization": f"Bearer {jwt_token}"}
    created = await client.post(base, headers=headers, json=create_body)
    assert created.status_code == 201, created.text
    item = f"{base}/{created.json()['id']}"
    updated = await client.patch(item, headers=headers, json=update_body)
    assert updated.status_code == 200, updated.text
    deleted = await client.delete(item, headers=headers)
    assert deleted.status_code == 204, deleted.text


async def test_jwt_reaches_ruleset_activation_handler(
    key_client: tuple[AsyncClient, str, str],
) -> None:
    """No ruleset is seeded here, so the handler's own 404 proves the guard let JWT through."""
    client, jwt_token, _raw_key = key_client
    resp = await client.patch(
        "/api/regulations/unknown/activate",
        headers={"Authorization": f"Bearer {jwt_token}"},
        json={"is_active": False},
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.parametrize("path", POLICY_READ_ROUTES)
async def test_api_key_can_read_masking_policy(
    key_client: tuple[AsyncClient, str, str], path: str
) -> None:
    client, _jwt, raw_key = key_client
    resp = await client.get(path, headers={HEADER_NAME: raw_key})
    assert resp.status_code == 200, resp.text


async def test_infrastructure_patch_rejects_api_key_without_middleware_skip(
    key_client: tuple[AsyncClient, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route guard holds even if /api/setup/ stops being middleware-exempt."""
    client, jwt_token, raw_key = key_client
    monkeypatch.setattr(
        auth_middleware,
        "_EXEMPT_PREFIXES",
        tuple(p for p in auth_middleware._EXEMPT_PREFIXES if p != "/api/setup/"),
    )
    blocked = await client.patch(
        "/api/setup/infrastructure", headers={HEADER_NAME: raw_key}, json={}
    )
    assert blocked.status_code == 403, blocked.text
    allowed = await client.patch(
        "/api/setup/infrastructure",
        headers={"Authorization": f"Bearer {jwt_token}"},
        json={},
    )
    assert allowed.status_code == 200, allowed.text


async def test_infrastructure_patch_rejects_api_key_by_default(
    key_client: tuple[AsyncClient, str, str],
) -> None:
    client, _jwt, raw_key = key_client
    resp = await client.patch(
        "/api/setup/infrastructure", headers={HEADER_NAME: raw_key}, json={}
    )
    assert resp.status_code in (401, 403), resp.text
