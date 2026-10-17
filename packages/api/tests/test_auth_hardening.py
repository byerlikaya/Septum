"""Hardening tests: un-encodable passwords, login timing, required JWT claims."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
import pytest
from httpx import AsyncClient
from septum_api.services import auth as auth_service
from septum_api.services.auth import (
    JWT_ALGORITHM,
    WeakPasswordError,
    decode_access_token,
    hash_password,
    validate_password_strength,
    verify_password,
)

ADMIN_EMAIL = "root@example.com"
ADMIN_PASSWORD = "password-12345"
LONE_SURROGATE_PASSWORD = "password-1234\ud800"


async def _register(client: AsyncClient) -> str:
    resp = await client.post(
        "/api/auth/register",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


async def _post_escaped_json(client: AsyncClient, path: str, body: dict):
    """POST JSON with ``\\uXXXX`` escapes so a lone surrogate reaches the server."""
    return await client.post(
        path,
        content=json.dumps(body, ensure_ascii=True),
        headers={"Content-Type": "application/json"},
    )


class TestLoneSurrogate:
    def test_validate_raises_weak_password_error(self) -> None:
        with pytest.raises(WeakPasswordError) as excinfo:
            validate_password_strength(LONE_SURROGATE_PASSWORD)
        assert LONE_SURROGATE_PASSWORD not in str(excinfo.value)

    def test_verify_returns_false(self) -> None:
        assert verify_password(LONE_SURROGATE_PASSWORD, hash_password(ADMIN_PASSWORD)) is False

    async def test_register_is_400(self, router_client: AsyncClient) -> None:
        resp = await _post_escaped_json(
            router_client,
            "/api/auth/register",
            {"email": ADMIN_EMAIL, "password": LONE_SURROGATE_PASSWORD},
        )
        assert resp.status_code == 400, resp.text

    async def test_login_is_401(self, router_client: AsyncClient) -> None:
        await _register(router_client)
        resp = await _post_escaped_json(
            router_client,
            "/api/auth/login",
            {"email": ADMIN_EMAIL, "password": LONE_SURROGATE_PASSWORD},
        )
        assert resp.status_code == 401, resp.text


class TestLoginTiming:
    async def test_unknown_email_still_runs_a_bcrypt_check(
        self, router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        checked_hashes: list[bytes] = []
        real_checkpw = bcrypt.checkpw

        def _spy(password: bytes, hashed: bytes) -> bool:
            checked_hashes.append(hashed)
            return real_checkpw(password, hashed)

        monkeypatch.setattr(auth_service.bcrypt, "checkpw", _spy)
        resp = await router_client.post(
            "/api/auth/login",
            json={"email": "nobody@example.com", "password": ADMIN_PASSWORD},
        )
        assert resp.status_code == 401
        assert checked_hashes == [auth_service._dummy_password_hash().encode()]

    def test_dummy_hash_uses_the_same_cost_as_real_hashes(self) -> None:
        real_cost = hash_password(ADMIN_PASSWORD).split("$")[2]
        assert auth_service._dummy_password_hash().split("$")[2] == real_cost


class TestRequiredJwtClaims:
    def _encode(self, payload: dict) -> str:
        return jwt.encode(payload, auth_service._jwt_secret(), algorithm=JWT_ALGORITHM)

    def test_token_without_exp_is_rejected(self) -> None:
        with pytest.raises(jwt.MissingRequiredClaimError):
            decode_access_token(self._encode({"sub": "1"}))

    def test_token_without_sub_is_rejected(self) -> None:
        exp = datetime.now(timezone.utc) + timedelta(minutes=5)
        with pytest.raises(jwt.MissingRequiredClaimError):
            decode_access_token(self._encode({"exp": exp}))

    async def test_endpoint_rejects_token_without_exp(
        self, router_client: AsyncClient
    ) -> None:
        await _register(router_client)
        token = self._encode({"sub": "1", "email": ADMIN_EMAIL})
        resp = await router_client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 401, resp.text
