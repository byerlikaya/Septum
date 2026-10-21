"""Tests for the setup wizard router."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from septum_api.bootstrap import SETUP_TOKEN_ENV, _invalidate_cache, save_config
from septum_api.main import app, limiter
from septum_api.utils.auth_dependency import SETUP_FORBIDDEN_DETAIL

TOKEN = "router-test-setup-token-0123456789"
client = TestClient(app, headers={"X-Setup-Token": TOKEN})
anonymous = TestClient(app)
FORBIDDEN = {"detail": SETUP_FORBIDDEN_DETAIL, "code": "setup_forbidden"}

SENTINEL_PASSWORD = "s3ntinel-Pa55w0rd"


@pytest.fixture(autouse=True)
def _isolated_bootstrap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate bootstrap config for each test."""
    config_path = tmp_path / "config.json"
    monkeypatch.setenv("SEPTUM_CONFIG_PATH", str(config_path))
    for env_var in (
        "DATABASE_URL", "DB_PATH", "REDIS_URL", "ENCRYPTION_KEY",
        "JWT_SECRET_KEY", "JWT_EXPIRATION_MINUTES", "LOG_LEVEL",
        "RATE_LIMIT_DEFAULT", "FRONTEND_ORIGIN",
    ):
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setenv(SETUP_TOKEN_ENV, TOKEN)
    _invalidate_cache()
    yield
    _invalidate_cache()


class TestSetupStatus:
    """GET /api/setup/status."""

    def test_needs_infrastructure_on_fresh_install(self):
        resp = client.get("/api/setup/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "needs_infrastructure"

    def test_needs_application_setup_when_db_configured(self, tmp_path: Path):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "encryption_key": "test",
            "jwt_secret_key": "test",
            "database_configured": True,
        }))
        _invalidate_cache()

        with patch("septum_api.routers.setup.engine_is_ready", return_value=True), \
             patch("septum_api.routers.setup.get_session_maker") as mock_sm:
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = False
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_sm.return_value = MagicMock(return_value=mock_session)

            resp = client.get("/api/setup/status")
            assert resp.status_code == 200
            assert resp.json()["status"] == "needs_application_setup"

    def test_complete_when_setup_finished(self, tmp_path: Path):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "encryption_key": "test",
            "jwt_secret_key": "test",
            "database_configured": True,
        }))
        _invalidate_cache()

        with patch("septum_api.routers.setup.engine_is_ready", return_value=True), \
             patch("septum_api.routers.setup.get_session_maker") as mock_sm:
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = True
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_sm.return_value = MagicMock(return_value=mock_session)

            resp = client.get("/api/setup/status")
            assert resp.status_code == 200
            assert resp.json()["status"] == "complete"


class TestTestDatabase:
    """POST /api/setup/test-database."""

    def test_rejects_empty_url(self):
        resp = client.post("/api/setup/test-database", json={"database_url": ""})
        assert resp.status_code == 400

    def test_returns_ok_on_success(self):
        with patch("septum_api.routers.setup.create_async_engine") as mock_engine:
            mock_conn = AsyncMock()
            mock_conn.execute = AsyncMock()
            mock_eng_instance = AsyncMock()
            mock_eng_instance.connect = MagicMock(return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_conn),
                __aexit__=AsyncMock(return_value=False),
            ))
            mock_eng_instance.dispose = AsyncMock()
            mock_engine.return_value = mock_eng_instance

            resp = client.post(
                "/api/setup/test-database",
                json={"database_url": "postgresql+asyncpg://u:p@host/db"},
            )
            assert resp.status_code == 200
            assert resp.json()["ok"] is True

    def test_returns_error_on_failure(self):
        with patch("septum_api.routers.setup.create_async_engine") as mock_engine:
            mock_engine.side_effect = Exception("Connection refused")
            resp = client.post(
                "/api/setup/test-database",
                json={"database_url": "postgresql+asyncpg://bad/url"},
            )
            assert resp.status_code == 200
            assert resp.json() == {
                "ok": False, "message": "Connection failed", "code": "connection_failed",
            }

    def test_failure_does_not_echo_dsn_password(self, caplog: pytest.LogCaptureFixture):
        dsn = f"postgresql+asyncpg://u:{SENTINEL_PASSWORD}@host/db"
        with patch("septum_api.routers.setup.create_async_engine") as mock_engine, \
             caplog.at_level(logging.DEBUG):
            mock_engine.side_effect = ConnectionError(f"could not connect to {dsn}")
            resp = client.post("/api/setup/test-database", json={"database_url": dsn})

        assert resp.json()["ok"] is False
        assert SENTINEL_PASSWORD not in resp.text
        assert SENTINEL_PASSWORD not in caplog.text
        assert "ConnectionError" in caplog.text


class TestTestRedis:
    """POST /api/setup/test-redis."""

    def test_rejects_empty_url(self):
        resp = client.post("/api/setup/test-redis", json={"redis_url": ""})
        assert resp.status_code == 400

    def test_returns_ok_on_success(self):
        mock_client = MagicMock()
        mock_client.ping = AsyncMock(return_value=True)
        mock_client.aclose = AsyncMock()

        mock_aioredis = MagicMock()
        mock_aioredis.from_url = MagicMock(return_value=mock_client)

        with patch("septum_api.routers.setup._get_redis_module", return_value=mock_aioredis):
            resp = client.post(
                "/api/setup/test-redis",
                json={"redis_url": "redis://localhost:6379/0"},
            )
            assert resp.status_code == 200
            assert resp.json()["ok"] is True


    def test_failure_does_not_echo_url_password(self, caplog: pytest.LogCaptureFixture):
        url = f"redis://:{SENTINEL_PASSWORD}@localhost:6379/0"
        mock_aioredis = MagicMock()
        mock_aioredis.from_url = MagicMock(side_effect=ValueError(f"bad url {url}"))

        with patch("septum_api.routers.setup._get_redis_module", return_value=mock_aioredis), \
             caplog.at_level(logging.DEBUG):
            resp = client.post("/api/setup/test-redis", json={"redis_url": url})

        assert resp.json() == {
            "ok": False, "message": "Connection failed", "code": "connection_failed",
        }
        assert SENTINEL_PASSWORD not in caplog.text
        assert "ValueError" in caplog.text


class TestInitialize:
    """POST /api/setup/initialize."""

    def test_sqlite_initialization(self, tmp_path: Path):
        with patch("septum_api.routers.setup.engine_is_ready", return_value=False), \
             patch("septum_api.routers.setup.initialize_engine") as mock_init, \
             patch("septum_api.routers.setup.init_db", new_callable=AsyncMock) as mock_seed:
            resp = client.post("/api/setup/initialize", json={
                "database_type": "sqlite",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True

            mock_init.assert_called_once()
            mock_seed.assert_called_once()

        _invalidate_cache()
        from septum_api.bootstrap import get_config
        config = get_config()
        assert config.database_configured is True
        assert config.database_url == ""

    def test_postgresql_initialization(self, tmp_path: Path):
        with patch("septum_api.routers.setup.engine_is_ready", return_value=False), \
             patch("septum_api.routers.setup.initialize_engine") as mock_init, \
             patch("septum_api.routers.setup.init_db", new_callable=AsyncMock), \
             patch("septum_api.routers.setup._run_alembic_upgrade", new_callable=AsyncMock) as mock_alembic:
            resp = client.post("/api/setup/initialize", json={
                "database_type": "postgresql",
                "database_url": "postgresql+asyncpg://u:p@host/db",
                "redis_url": "redis://localhost:6379/0",
            })
            assert resp.status_code == 200
            assert resp.json()["ok"] is True

            mock_alembic.assert_called_once()
            mock_init.assert_called_once()

        _invalidate_cache()
        from septum_api.bootstrap import get_config
        config = get_config()
        assert config.database_configured is True
        assert config.database_url == "postgresql+asyncpg://u:p@host/db"
        assert config.redis_url == "redis://localhost:6379/0"

    def test_closed_once_infrastructure_is_configured(self):
        save_config({"database_configured": True})
        resp = client.post("/api/setup/initialize", json={"database_type": "sqlite"})
        assert resp.status_code == 403
        assert resp.json() == FORBIDDEN

    @pytest.mark.parametrize("body", [
        {"database_type": "postgresql", "database_url": "sqlite:////tmp/planted.db"},
        {"database_type": "sqlite", "redis_url": "http://internal.example/"},
    ])
    def test_rejects_disallowed_scheme(self, body: dict):
        with patch("septum_api.routers.setup.initialize_engine") as mock_init:
            resp = client.post("/api/setup/initialize", json=body)
        assert resp.status_code == 400
        assert resp.json()["code"] == "unsupported_url_scheme"
        mock_init.assert_not_called()

    def test_failure_does_not_echo_dsn_password(self, caplog: pytest.LogCaptureFixture):
        dsn = f"postgresql+asyncpg://u:{SENTINEL_PASSWORD}@host/db"
        with patch("septum_api.routers.setup.engine_is_ready", return_value=False), \
             patch("septum_api.routers.setup.initialize_engine"), \
             patch("septum_api.routers.setup.init_db", new_callable=AsyncMock), \
             patch("septum_api.routers.setup.reset_engine", new_callable=AsyncMock), \
             patch(
                 "septum_api.routers.setup._run_alembic_upgrade",
                 new_callable=AsyncMock,
                 side_effect=RuntimeError(f"Alembic migration failed: cannot reach {dsn}"),
             ), \
             caplog.at_level(logging.DEBUG):
            resp = client.post("/api/setup/initialize", json={
                "database_type": "postgresql",
                "database_url": dsn,
            })

        assert resp.json() == {"ok": False, "message": "Infrastructure initialisation failed"}
        assert SENTINEL_PASSWORD not in caplog.text
        assert all(record.exc_info is None for record in caplog.records)


    def test_failed_initialize_leaves_setup_open_for_a_retry(self):
        with patch("septum_api.routers.setup.engine_is_ready", return_value=False), \
             patch("septum_api.routers.setup.initialize_engine"), \
             patch("septum_api.routers.setup.init_db", new_callable=AsyncMock), \
             patch("septum_api.routers.setup.reset_engine", new_callable=AsyncMock) as reset, \
             patch(
                 "septum_api.routers.setup._run_alembic_upgrade",
                 new_callable=AsyncMock,
                 side_effect=RuntimeError("migration failed"),
             ):
            failed = client.post("/api/setup/initialize", json={
                "database_type": "postgresql",
                "database_url": "postgresql+asyncpg://u:p@host/db",
                "redis_url": "redis://localhost:6379/0",
            })
        assert failed.json() == {"ok": False, "message": "Infrastructure initialisation failed"}
        reset.assert_awaited_once()

        _invalidate_cache()
        from septum_api.bootstrap import get_config
        config = get_config()
        assert config.database_configured is False
        assert config.database_url == ""
        assert config.redis_url == ""

        with patch("septum_api.routers.setup.engine_is_ready", return_value=False), \
             patch("septum_api.routers.setup.initialize_engine"), \
             patch("septum_api.routers.setup.init_db", new_callable=AsyncMock):
            retry = client.post("/api/setup/initialize", json={"database_type": "sqlite"})
        assert retry.status_code == 200
        assert retry.json()["ok"] is True
        _invalidate_cache()
        assert get_config().database_configured is True


async def test_concurrent_initialize_configures_once():
    import asyncio

    from httpx import ASGITransport, AsyncClient

    async def slow_seed():
        await asyncio.sleep(0.05)

    with patch("septum_api.routers.setup.initialize_engine") as init_engine, \
         patch("septum_api.routers.setup.init_db", side_effect=slow_seed):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers={"X-Setup-Token": TOKEN}
        ) as http:
            responses = await asyncio.gather(*(
                http.post("/api/setup/initialize", json={"database_type": "sqlite"}) for _ in range(2)
            ))
    assert sorted(r.status_code for r in responses) == [200, 403]
    init_engine.assert_called_once()


def test_failure_before_the_engine_exists_leaves_the_engine_alone():
    with patch("septum_api.routers.setup.build_database_url", side_effect=RuntimeError("bad")), \
         patch("septum_api.routers.setup.reset_engine", new_callable=AsyncMock) as reset:
        resp = client.post("/api/setup/initialize", json={"database_type": "sqlite"})
    assert resp.json()["ok"] is False
    reset.assert_not_awaited()


async def test_reset_engine_disposes_and_forgets(monkeypatch):
    from septum_api import database

    engine = MagicMock()
    engine.dispose = AsyncMock()
    monkeypatch.setattr(database, "_engine", engine)
    monkeypatch.setattr(database, "_session_maker", MagicMock())
    await database.reset_engine()
    engine.dispose.assert_awaited_once()
    assert database.engine_is_ready() is False


class TestStatusLogging:
    """GET /api/setup/status must not log driver error text."""

    def test_query_failure_logs_class_only(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({"database_configured": True}))
        _invalidate_cache()

        with patch("septum_api.routers.setup.engine_is_ready", return_value=True), \
             patch(
                 "septum_api.routers.setup.get_session_maker",
                 side_effect=ConnectionError(f"password={SENTINEL_PASSWORD}"),
             ), \
             caplog.at_level(logging.DEBUG):
            resp = client.get("/api/setup/status")

        assert resp.json()["status"] == "needs_application_setup"
        assert SENTINEL_PASSWORD not in caplog.text
        assert "ConnectionError" in caplog.text


_INFRA_CALLS = [
    ("/api/setup/test-database", {"database_url": "postgresql+asyncpg://u:p@host/db"}),
    ("/api/setup/test-redis", {"redis_url": "redis://localhost:6379/0"}),
    ("/api/setup/initialize", {"database_type": "sqlite"}),
]


class TestInfrastructureGate:
    """test-database, test-redis and initialize share one gate."""

    @pytest.mark.parametrize(("path", "body"), _INFRA_CALLS)
    def test_missing_token_is_403_even_from_loopback(self, path: str, body: dict):
        resp = anonymous.post(path, json=body)
        assert resp.status_code == 403
        assert resp.json() == FORBIDDEN

    @pytest.mark.parametrize(("path", "body"), _INFRA_CALLS)
    def test_wrong_token_is_403(self, path: str, body: dict):
        resp = anonymous.post(path, json=body, headers={"X-Setup-Token": TOKEN + "x"})
        assert resp.status_code == 403
        assert resp.json() == FORBIDDEN

    @pytest.mark.parametrize(("path", "body"), _INFRA_CALLS)
    def test_no_token_configured_is_403(
        self, path: str, body: dict, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv(SETUP_TOKEN_ENV)
        resp = client.post(path, json=body)
        assert resp.status_code == 403
        assert resp.json() == FORBIDDEN

    @pytest.mark.parametrize(("path", "body"), _INFRA_CALLS)
    def test_token_rejected_once_infrastructure_is_configured(self, path: str, body: dict):
        save_config({"database_configured": True})
        resp = client.post(path, json=body)
        assert resp.status_code == 403
        assert resp.json() == FORBIDDEN

    def test_stored_token_is_accepted(self, monkeypatch: pytest.MonkeyPatch):
        from septum_api.bootstrap import ensure_setup_token

        monkeypatch.delenv(SETUP_TOKEN_ENV)
        stored = ensure_setup_token()
        with patch("septum_api.routers.setup._get_redis_module") as mock_mod:
            mock_client = MagicMock(ping=AsyncMock(return_value=True), aclose=AsyncMock())
            mock_mod.return_value = MagicMock(from_url=MagicMock(return_value=mock_client))
            resp = anonymous.post(
                "/api/setup/test-redis",
                json={"redis_url": "redis://localhost:6379/0"},
                headers={"X-Setup-Token": stored},
            )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


class TestSchemeAllowlist:
    """Only PostgreSQL and Redis URLs may be probed."""

    @pytest.mark.parametrize("url", [
        "sqlite+aiosqlite:////tmp/planted.db",
        "mysql://u:p@host/db",
        "http://169.254.169.254/latest",
        "file:///etc/passwd",
    ])
    def test_database_rejects(self, url: str):
        with patch("septum_api.routers.setup.create_async_engine") as mock_engine:
            resp = client.post("/api/setup/test-database", json={"database_url": url})
        assert resp.status_code == 400
        assert resp.json()["code"] == "unsupported_url_scheme"
        assert url not in resp.text
        mock_engine.assert_not_called()

    @pytest.mark.parametrize("url", ["http://host:6379", "unix:///tmp/redis.sock", "memcached://h"])
    def test_redis_rejects(self, url: str):
        with patch("septum_api.routers.setup._get_redis_module") as mock_mod:
            resp = client.post("/api/setup/test-redis", json={"redis_url": url})
        assert resp.status_code == 400
        assert resp.json()["code"] == "unsupported_url_scheme"
        mock_mod.assert_not_called()

    @pytest.mark.parametrize("url", ["postgresql://u:p@host/db", "POSTGRESQL+ASYNCPG://u:p@h/db"])
    def test_database_accepts(self, url: str):
        with patch("septum_api.routers.setup.create_async_engine", side_effect=OSError):
            resp = client.post("/api/setup/test-database", json={"database_url": url})
        assert resp.status_code == 200

    @pytest.mark.parametrize("url", ["redis://localhost:6379/0", "rediss://localhost:6380/0"])
    def test_redis_accepts(self, url: str):
        mock_aioredis = MagicMock(from_url=MagicMock(side_effect=OSError))
        with patch("septum_api.routers.setup._get_redis_module", return_value=mock_aioredis):
            resp = client.post("/api/setup/test-redis", json={"redis_url": url})
        assert resp.status_code == 200


class TestVerifyToken:
    """POST /api/setup/verify-token."""

    @pytest.fixture(autouse=True)
    def _fresh_limits(self):
        limiter.reset()
        yield
        limiter.reset()

    def test_match_is_204(self):
        resp = client.post("/api/setup/verify-token")
        assert resp.status_code == 204
        assert resp.content == b""

    @pytest.mark.parametrize("headers", [{}, {"X-Setup-Token": "nope"}, {"X-Setup-Token": ""}])
    def test_mismatch_is_generic_403(self, headers: dict):
        resp = anonymous.post("/api/setup/verify-token", headers=headers)
        assert resp.status_code == 403
        assert resp.json() == FORBIDDEN

    def test_closed_setup_is_403(self):
        save_config({"database_configured": True})
        with patch("septum_api.utils.auth_dependency.is_setup_open", AsyncMock(return_value=False)):
            resp = client.post("/api/setup/verify-token")
        assert resp.status_code == 403
        assert resp.json() == FORBIDDEN

    def test_open_after_infrastructure_when_database_says_so(self):
        save_config({"database_configured": True})
        with patch("septum_api.utils.auth_dependency.is_setup_open", AsyncMock(return_value=True)):
            resp = client.post("/api/setup/verify-token")
        assert resp.status_code == 204

    def test_ten_wrong_guesses_per_minute_then_429_but_the_token_still_works(self):
        codes = [
            anonymous.post("/api/setup/verify-token", headers={"X-Setup-Token": f"g{i}"}).status_code
            for i in range(10)
        ]
        assert codes == [403] * 10
        assert anonymous.post("/api/setup/verify-token", headers={"X-Setup-Token": "g"}).status_code == 429
        assert client.post("/api/setup/verify-token").status_code == 204


class TestSetupTimeEndpointsGate:
    """Bootstrap-window settings, regulations and whisper endpoints require the token."""

    _CALLS = [
        ("get", "/api/setup/whisper-status", None),
        ("patch", "/api/settings", {"llm_model": "x"}),
        ("get", "/api/regulations", None),
    ]

    @pytest.mark.parametrize(
        ("method", "path", "body"),
        [*_CALLS, ("post", "/api/setup/install-whisper", {"model": "base"})],
    )
    async def test_without_token_is_403(self, router_client, method: str, path: str, body):
        router_client.headers.pop("X-Setup-Token")
        kwargs = {"json": body} if body is not None else {}
        resp = await getattr(router_client, method)(path, **kwargs)
        assert resp.status_code == 403
        assert resp.json() == FORBIDDEN

    @pytest.mark.parametrize(("method", "path", "body"), _CALLS)
    async def test_with_token_passes_the_gate(self, router_client, method: str, path: str, body):
        kwargs = {"json": body} if body is not None else {}
        resp = await getattr(router_client, method)(path, **kwargs)
        assert resp.status_code != 403


def _routes_depending_on(*targets) -> list[tuple[str, str]]:
    """Return ``(method, path)`` for every app route whose dependency tree includes one of *targets*."""
    from fastapi.routing import APIRoute
    from septum_api import main

    def _uses(dependant) -> bool:
        return any(d.call in targets or _uses(d) for d in dependant.dependencies)

    candidates = [*app.routes, *(r for router in main._all_routers for r in router.routes)]
    return sorted({
        (method.lower(), route.path)
        for route in candidates
        if isinstance(route, APIRoute) and _uses(route.dependant)
        for method in route.methods
    })


def _bootstrap_gated_routes() -> list[tuple[str, str]]:
    from septum_api.utils.auth_dependency import (
        require_admin_or_bootstrap,
        require_user_or_bootstrap,
    )

    return _routes_depending_on(require_admin_or_bootstrap, require_user_or_bootstrap)


class TestEveryBootstrapGatedRoute:
    """Each route behind the bootstrap dependencies refuses a token-less caller while setup is open."""

    def test_known_setup_time_routes_are_discovered(self):
        routes = set(_bootstrap_gated_routes())
        assert {
            ("patch", "/api/settings"),
            ("post", "/api/settings/test-llm"),
            ("post", "/api/settings/ollama-pull"),
            ("get", "/api/setup/whisper-status"),
            ("post", "/api/setup/install-whisper"),
            ("get", "/api/regulations"),
            ("patch", "/api/regulations/{ruleset_id}/activate"),
        } <= routes

    @pytest.mark.parametrize(("method", "path"), _bootstrap_gated_routes())
    async def test_without_token_is_generic_403(self, router_client, method: str, path: str):
        import re

        router_client.headers.pop("X-Setup-Token")
        url = re.sub(r"\{[^}]+\}", "GDPR", path)
        kwargs = {"json": {}} if method in {"post", "patch", "put"} else {}
        resp = await router_client.request(method.upper(), url, **kwargs)
        assert resp.status_code == 403
        assert resp.json() == FORBIDDEN

    @pytest.mark.parametrize(("method", "path"), _bootstrap_gated_routes())
    async def test_wrong_token_is_generic_403(self, router_client, method: str, path: str):
        import re

        url = re.sub(r"\{[^}]+\}", "GDPR", path)
        kwargs = {"json": {}} if method in {"post", "patch", "put"} else {}
        resp = await router_client.request(
            method.upper(), url, headers={"X-Setup-Token": "not-the-token"}, **kwargs
        )
        assert resp.status_code == 403
        assert resp.json() == FORBIDDEN
