"""Tests for the bcrypt 72-byte password ceiling across every hashing path."""

from __future__ import annotations

import bcrypt
import pytest
from httpx import AsyncClient
from septum_api.services.auth import (
    PASSWORD_MAX_BYTES,
    WeakPasswordError,
    hash_password,
    validate_password_strength,
    verify_password,
)

ADMIN_EMAIL = "root@example.com"
ADMIN_PASSWORD = "password-12345"

MULTIBYTE_OK = "ş" * 24  # 48 bytes
MULTIBYTE_AT_LIMIT = "ş" * 36  # 72 bytes
MULTIBYTE_TOO_LONG = "ş" * 37  # 74 bytes
ASCII_AT_LIMIT = "a" * 72
ASCII_TOO_LONG = "a" * 73


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _bootstrap_admin(client: AsyncClient) -> str:
    resp = await client.post(
        "/api/auth/register",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _assert_rejected_without_echo(resp, password: str) -> None:
    assert resp.status_code == 400, resp.text
    assert password not in resp.text
    assert str(PASSWORD_MAX_BYTES) in resp.json()["detail"]


class TestValidatePasswordStrength:
    @pytest.mark.parametrize(
        "password", [ASCII_AT_LIMIT, MULTIBYTE_OK, MULTIBYTE_AT_LIMIT]
    )
    def test_accepts_up_to_limit(self, password: str) -> None:
        validate_password_strength(password)

    @pytest.mark.parametrize("password", [ASCII_TOO_LONG, MULTIBYTE_TOO_LONG])
    def test_rejects_over_limit_without_echo(self, password: str) -> None:
        with pytest.raises(WeakPasswordError) as excinfo:
            validate_password_strength(password)
        assert password not in str(excinfo.value)

    def test_byte_length_not_char_length(self) -> None:
        assert len(MULTIBYTE_TOO_LONG) < PASSWORD_MAX_BYTES
        assert len(MULTIBYTE_TOO_LONG.encode()) > PASSWORD_MAX_BYTES


class TestVerifyPassword:
    def test_fresh_hash_verifies(self) -> None:
        hashed = hash_password(MULTIBYTE_AT_LIMIT)
        assert verify_password(MULTIBYTE_AT_LIMIT, hashed)
        assert not verify_password(MULTIBYTE_OK, hashed)

    def test_legacy_hash_of_long_password_still_verifies(self) -> None:
        # bcrypt<5 silently hashed only the first 72 bytes.
        legacy_password = "x" * 90
        legacy_hash = bcrypt.hashpw(
            legacy_password.encode()[:PASSWORD_MAX_BYTES], bcrypt.gensalt(4)
        ).decode()
        assert verify_password(legacy_password, legacy_hash)

    def test_over_long_input_returns_false_instead_of_raising(self) -> None:
        hashed = hash_password(ADMIN_PASSWORD)
        assert verify_password("y" * 200, hashed) is False


class TestEndpointsRejectOverLongPasswords:
    async def test_register(self, router_client: AsyncClient) -> None:
        resp = await router_client.post(
            "/api/auth/register",
            json={"email": ADMIN_EMAIL, "password": MULTIBYTE_TOO_LONG},
        )
        _assert_rejected_without_echo(resp, MULTIBYTE_TOO_LONG)

    async def test_register_multibyte_within_limit_round_trips(
        self, router_client: AsyncClient
    ) -> None:
        resp = await router_client.post(
            "/api/auth/register",
            json={"email": ADMIN_EMAIL, "password": MULTIBYTE_OK},
        )
        assert resp.status_code == 201, resp.text
        login = await router_client.post(
            "/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": MULTIBYTE_OK},
        )
        assert login.status_code == 200, login.text

    async def test_login_with_over_long_password_is_401(
        self, router_client: AsyncClient
    ) -> None:
        await _bootstrap_admin(router_client)
        resp = await router_client.post(
            "/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": "z" * 200},
        )
        assert resp.status_code == 401, resp.text

    async def test_change_password_new_too_long(
        self, router_client: AsyncClient
    ) -> None:
        token = await _bootstrap_admin(router_client)
        resp = await router_client.post(
            "/api/auth/change-password",
            headers=_auth(token),
            json={"current_password": ADMIN_PASSWORD, "new_password": ASCII_TOO_LONG},
        )
        _assert_rejected_without_echo(resp, ASCII_TOO_LONG)

    async def test_change_password_current_too_long_is_400(
        self, router_client: AsyncClient
    ) -> None:
        token = await _bootstrap_admin(router_client)
        resp = await router_client.post(
            "/api/auth/change-password",
            headers=_auth(token),
            json={"current_password": "c" * 200, "new_password": MULTIBYTE_OK},
        )
        assert resp.status_code == 400, resp.text

    async def test_admin_create_user(self, router_client: AsyncClient) -> None:
        token = await _bootstrap_admin(router_client)
        resp = await router_client.post(
            "/api/users",
            headers=_auth(token),
            json={
                "email": "new@example.com",
                "password": MULTIBYTE_TOO_LONG,
                "role": "editor",
            },
        )
        _assert_rejected_without_echo(resp, MULTIBYTE_TOO_LONG)

    async def test_admin_reset_password(self, router_client: AsyncClient) -> None:
        token = await _bootstrap_admin(router_client)
        created = await router_client.post(
            "/api/users",
            headers=_auth(token),
            json={"email": "u@example.com", "password": ADMIN_PASSWORD, "role": "viewer"},
        )
        assert created.status_code == 201, created.text
        resp = await router_client.post(
            f"/api/users/{created.json()['id']}/reset-password",
            headers=_auth(token),
            json={"new_password": ASCII_TOO_LONG},
        )
        _assert_rejected_without_echo(resp, ASCII_TOO_LONG)
