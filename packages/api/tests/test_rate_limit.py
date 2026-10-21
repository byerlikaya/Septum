"""Rate limiting: identity keys, proxy trust, route and default limits, failure buckets, outages."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from limits import parse
from septum_api.database import get_db
from septum_api.main import app, default_rate_limit
from septum_api.middleware import rate_limit
from septum_api.middleware.rate_limit import (
    DEFAULT_LIMIT_EXEMPT_ENDPOINTS,
    get_limiter,
    get_rate_limit_key,
)
from septum_api.models import Base
from septum_api.models.user import User
from septum_api.services.auth import create_access_token, hash_password
from septum_api.utils.client_ip import resolve_client_ip
from septum_api.utils.crypto import hash_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

PROXY = "127.0.0.1"
UNTRUSTED_PEER = "203.0.113.9"
VERIFY_TOKEN_LIMIT = 10


def _client(peer: str) -> AsyncClient:
    """Client whose requests reach the app through uvicorn's proxy-header handling."""
    wrapped = ProxyHeadersMiddleware(app, trusted_hosts=PROXY)
    return AsyncClient(
        transport=ASGITransport(app=wrapped, client=(peer, 40000)),
        base_url="http://test",
    )


@pytest.fixture
def limiter():
    """The shared limiter, enabled and empty for the test; its prior state comes back after."""
    shared = get_limiter()
    was_enabled = shared.enabled
    shared.enabled = True
    shared.reset()
    yield shared
    shared.reset()
    shared.enabled = was_enabled


@pytest.fixture
def tight_default_limit(limiter, monkeypatch: pytest.MonkeyPatch):
    """Lower the default limit to two requests per minute."""
    monkeypatch.setattr(default_rate_limit, "item", parse("2/minute"))


@pytest.fixture
async def isolated_db(tmp_path: Path) -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rl.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _override():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = _override
    yield session_maker
    app.dependency_overrides.pop(get_db, None)
    await engine.dispose()


async def _spend_verify_token(client: AsyncClient, xff: str | None = None) -> list[int]:
    """Send one more than the verify-token limit with a wrong token; return status codes."""
    codes = []
    for i in range(VERIFY_TOKEN_LIMIT + 1):
        headers = {"X-Setup-Token": "wrong"}
        if xff is not None:
            headers["X-Forwarded-For"] = xff.format(i=i)
        codes.append((await client.post("/api/setup/verify-token", headers=headers)).status_code)
    return codes


def _fake_request(**state) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(**state),
        headers={},
        client=SimpleNamespace(host="198.51.100.4", port=1),
    )


class TestKeyFunction:
    def test_api_key_wins_over_user(self):
        request = _fake_request(auth_method="api_key", auth_user_id=7)
        request.headers = {"x-api-key": "sk-septum-abcdef12" + "0" * 56}
        assert get_rate_limit_key(request) == "apikey:abcdef12"

    def test_user_id_wins_over_address(self):
        assert get_rate_limit_key(_fake_request(auth_method="jwt", auth_user_id=7)) == "user:7"

    def test_anonymous_falls_back_to_address(self):
        request = _fake_request(auth_method=None, auth_user_id=None)
        assert get_rate_limit_key(request) == f"ip:{hash_text('198.51.100.4')}"

    @pytest.mark.parametrize(
        ("host", "expected"),
        [("::ffff:192.0.2.7", "192.0.2.7"), ("2001:db8::1", "2001:db8::1"), ("192.0.2.7", "192.0.2.7")],
    )
    def test_ipv4_mapped_address_is_folded(self, host: str, expected: str):
        assert resolve_client_ip(SimpleNamespace(client=SimpleNamespace(host=host))) == expected

    def test_missing_peer_is_none(self):
        assert resolve_client_ip(SimpleNamespace(client=None)) is None


class TestProxyTrust:
    async def test_forged_xff_from_untrusted_peer_keeps_one_bucket(self, limiter):
        async with _client(UNTRUSTED_PEER) as client:
            codes = await _spend_verify_token(client, xff="10.0.0.{i}")
        assert codes[:-1] == [403] * VERIFY_TOKEN_LIMIT
        assert codes[-1] == 429

    async def test_trusted_proxy_separates_clients(self, limiter):
        async with _client(PROXY) as client:
            first = await _spend_verify_token(client, xff="192.0.2.10")
            second = await client.post(
                "/api/setup/verify-token",
                headers={"X-Setup-Token": "wrong", "X-Forwarded-For": "192.0.2.11"},
            )
        assert first[-1] == 429
        assert second.status_code == 403

    async def test_ipv4_mapped_and_plain_share_a_bucket(self, limiter):
        async with _client(PROXY) as client:
            for _ in range(VERIFY_TOKEN_LIMIT):
                await client.post(
                    "/api/setup/verify-token",
                    headers={"X-Setup-Token": "wrong", "X-Forwarded-For": "::ffff:192.0.2.20"},
                )
            resp = await client.post(
                "/api/setup/verify-token",
                headers={"X-Setup-Token": "wrong", "X-Forwarded-For": "192.0.2.20"},
            )
        assert resp.status_code == 429


class TestDefaultLimit:
    async def test_global_limit_returns_429(self, tight_default_limit, isolated_db):
        async with _client(UNTRUSTED_PEER) as client:
            codes = [(await client.get("/api/auth/me")).status_code for _ in range(3)]
        assert 429 not in codes[:2]
        assert codes[2] == 429

    async def test_users_on_one_address_get_separate_buckets(self, tight_default_limit, isolated_db):
        alice = {"Authorization": f"Bearer {create_access_token(1, 'a@example.com')}"}
        bob = {"Authorization": f"Bearer {create_access_token(2, 'b@example.com')}"}
        async with _client(UNTRUSTED_PEER) as client:
            alice_codes = [(await client.get("/api/auth/me", headers=alice)).status_code for _ in range(3)]
            bob_code = (await client.get("/api/auth/me", headers=bob)).status_code
        assert alice_codes[2] == 429
        assert bob_code != 429

    @pytest.mark.parametrize("path", ["/health", "/metrics", "/api/setup/status"])
    async def test_exempt_paths_are_not_limited(self, tight_default_limit, isolated_db, path: str):
        async with _client(UNTRUSTED_PEER) as client:
            codes = [(await client.get(path)).status_code for _ in range(4)]
        assert 429 not in codes

    async def test_exemption_follows_the_endpoint_under_a_root_path(self, tight_default_limit):
        from septum_api.main import health

        request = _fake_request(auth_method=None, auth_user_id=None)
        request.scope = {"endpoint": health, "root_path": "/septum"}
        request.url = SimpleNamespace(path="/septum/health")
        for _ in range(5):
            await default_rate_limit(request)

    async def test_exempt_path_on_another_endpoint_is_still_limited(self, tight_default_limit):
        def endpoint():
            """Stand-in endpoint that is not a probe."""

        request = _fake_request(auth_method=None, auth_user_id=None)
        request.scope = {"endpoint": endpoint}
        request.url = SimpleNamespace(path="/api/setup/status")
        await default_rate_limit(request)
        await default_rate_limit(request)
        with pytest.raises(rate_limit.RateLimited):
            await default_rate_limit(request)

    async def test_dependency_counts_a_non_exempt_path(self, tight_default_limit):
        def endpoint():
            """Stand-in undecorated endpoint."""

        request = _fake_request(auth_method=None, auth_user_id=None)
        request.scope = {"endpoint": endpoint}
        request.url = SimpleNamespace(path="/api/documents")
        await default_rate_limit(request)
        await default_rate_limit(request)
        with pytest.raises(rate_limit.RateLimited):
            await default_rate_limit(request)

    async def test_route_limit_replaces_the_default(self, tight_default_limit, monkeypatch):
        def endpoint():
            """Stand-in endpoint with its own limit."""

        monkeypatch.setitem(
            rate_limit.ROUTE_LIMITS,
            f"{endpoint.__module__}.{endpoint.__qualname__}",
            rate_limit.RouteLimit(parse("4/minute"), fail_open=True),
        )
        request = _fake_request(auth_method=None, auth_user_id=None)
        request.scope = {"endpoint": endpoint}
        request.url = SimpleNamespace(path="/api/documents")
        for _ in range(4):
            await default_rate_limit(request)
        with pytest.raises(rate_limit.RateLimited):
            await default_rate_limit(request)

    async def test_route_limit_follows_the_endpoint_not_the_path(self, limiter, monkeypatch):
        from septum_api.routers.chat import chat_ask

        monkeypatch.setitem(
            rate_limit.ROUTE_LIMITS,
            rate_limit._endpoint_name(chat_ask),
            rate_limit.RouteLimit(parse("1/minute"), fail_open=True),
        )
        request = _fake_request(auth_method=None, auth_user_id=None)
        request.scope = {"endpoint": chat_ask}
        request.url = SimpleNamespace(path="/mounted/under/a/root-path/api/chat/ask")
        await default_rate_limit(request)
        with pytest.raises(rate_limit.RateLimited):
            await default_rate_limit(request)

    def test_every_exempt_endpoint_exists(self):
        from septum_api import main
        from septum_api.routers import setup
        from septum_api.utils import metrics

        endpoints = {main.health, metrics.metrics_endpoint}
        assert {rate_limit._endpoint_name(e) for e in endpoints} == DEFAULT_LIMIT_EXEMPT_ENDPOINTS
        assert rate_limit._endpoint_name(setup.setup_status) not in DEFAULT_LIMIT_EXEMPT_ENDPOINTS

    async def test_verify_token_is_limited_by_setup_token_failures(self, limiter):
        async with _client(UNTRUSTED_PEER) as client:
            codes = await _spend_verify_token(client)
        assert codes[:-1] == [403] * VERIFY_TOKEN_LIMIT
        assert codes[-1] == 429


VICTIM_EMAIL = "victim@example.com"
VICTIM_PASSWORD = "correct-password-123"
WRONG_PASSWORD = "wrong-password-1"


async def _create_victim(session_maker: async_sessionmaker) -> None:
    async with session_maker() as db:
        db.add(User(email=VICTIM_EMAIL, hashed_password=hash_password(VICTIM_PASSWORD), role="admin"))
        await db.commit()


async def _login(client: AsyncClient, address: str, password: str, email: str = VICTIM_EMAIL) -> int:
    resp = await client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
        headers={"X-Forwarded-For": address},
    )
    return resp.status_code


class TestLoginFailureLimit:
    async def test_spent_ceiling_blocks_the_attacker_but_not_the_owner(
        self, limiter, isolated_db, monkeypatch
    ):
        monkeypatch.setattr(rate_limit, "LOGIN_EMAIL_FAILURE_CEILING", parse("4/hour"))
        await _create_victim(isolated_db)
        async with _client(PROXY) as client:
            attack = [await _login(client, "192.0.2.1", WRONG_PASSWORD) for _ in range(4)]
            attacker_again = await _login(client, "192.0.2.1", VICTIM_PASSWORD)
            owner = await _login(client, "192.0.2.2", VICTIM_PASSWORD)
        assert attack == [401] * 4
        assert attacker_again == 429
        assert owner == 200

    async def test_ceiling_spans_addresses_and_case_but_spares_clean_clients(
        self, limiter, isolated_db, monkeypatch
    ):
        monkeypatch.setattr(rate_limit, "LOGIN_EMAIL_FAILURE_CEILING", parse("4/hour"))
        await _create_victim(isolated_db)
        async with _client(PROXY) as client:
            failures = [
                await _login(
                    client,
                    f"192.0.2.{i + 1}",
                    WRONG_PASSWORD,
                    VICTIM_EMAIL.upper() if i % 2 else VICTIM_EMAIL,
                )
                for i in range(4)
            ]
            failer = await _login(client, "192.0.2.3", VICTIM_PASSWORD)
            clean = await _login(client, "192.0.2.50", VICTIM_PASSWORD)
            other = await _login(client, "192.0.2.51", WRONG_PASSWORD, "other@example.com")
        assert failures == [401] * 4
        assert failer == 429
        assert clean == 200
        assert other == 401

    async def test_attackers_on_two_addresses_do_not_lock_out_a_third(
        self, limiter, isolated_db
    ):
        await _create_victim(isolated_db)
        async with _client(PROXY) as client:
            attack = [
                await _login(client, address, WRONG_PASSWORD)
                for address in ("192.0.2.1", "192.0.2.2")
                for _ in range(5)
            ]
            owner = await _login(client, "192.0.2.3", VICTIM_PASSWORD)
        assert set(attack) == {401}
        assert owner == 200

    async def test_successful_logins_are_not_counted(self, limiter, isolated_db, monkeypatch):
        monkeypatch.setattr(rate_limit, "LOGIN_EMAIL_FAILURE_CEILING", parse("1/hour"))
        await _create_victim(isolated_db)
        async with _client(PROXY) as client:
            successes = [await _login(client, "192.0.2.1", VICTIM_PASSWORD) for _ in range(3)]
            first_failure = await _login(client, "192.0.2.1", WRONG_PASSWORD)
        assert successes == [200, 200, 200]
        assert first_failure == 401


class TestNoRawIdentityStored:
    async def test_storage_and_logs_hold_no_email_or_address(self, limiter, isolated_db, caplog):
        async with _client(PROXY) as client:
            with caplog.at_level(logging.DEBUG):
                await _login(client, "192.0.2.77", WRONG_PASSWORD)
                await _spend_verify_token(client, xff="192.0.2.77")
        keys = " ".join(limiter.buckets._primary.storage.storage.keys())
        assert "login-email-fail" in keys and "login-client-fail" in keys
        assert "victim" not in keys.lower()
        assert "192.0.2.77" not in keys
        assert "192.0.2.77" not in caplog.text


class TestSetupTokenFailures:
    async def test_failures_share_one_bucket_across_gated_routes(self, limiter, isolated_db):
        wrong = {"X-Setup-Token": "wrong"}
        async with _client(UNTRUSTED_PEER) as client:
            codes = [
                (await client.post("/api/setup/verify-token", headers=wrong)).status_code
                for _ in range(5)
            ]
            codes += [
                (await client.get("/api/setup/whisper-status", headers=wrong)).status_code
                for _ in range(5)
            ]
            stream = await client.post("/api/setup/install-whisper", json={}, headers=wrong)
        async with _client("203.0.113.10") as other_client:
            other = await other_client.post("/api/setup/verify-token", headers=wrong)
        assert codes == [403] * 10
        assert stream.status_code == 429
        assert other.status_code == 403


class _BrokenStorage:
    """Strategy stand-in whose every call fails like an unreachable Redis."""

    def __init__(self) -> None:
        self.calls = 0

    async def hit(self, *args, **kwargs):
        self.calls += 1
        raise ConnectionError("storage down")

    test = hit


class TestStorageOutage:
    @pytest.fixture
    def broken(self, monkeypatch: pytest.MonkeyPatch) -> tuple[rate_limit.AsyncBuckets, _BrokenStorage]:
        buckets = rate_limit.AsyncBuckets("memory://")
        primary = _BrokenStorage()
        monkeypatch.setattr(buckets, "_primary", primary)
        return buckets, primary

    async def test_general_limits_fail_open(self, broken):
        buckets, _ = broken
        item = parse("1/minute")
        assert all([await buckets.hit(item, "k", fail_open=True) for _ in range(5)])

    async def test_failure_buckets_stay_enforced_in_memory(self, broken):
        buckets, _ = broken
        item = parse("2/minute")
        results = [await buckets.hit(item, "k", fail_open=False) for _ in range(3)]
        assert results == [True, True, False]

    async def test_outage_is_logged_once_and_storage_retried_later(self, broken, caplog, monkeypatch):
        buckets, primary = broken
        item = parse("5/minute")
        with caplog.at_level(logging.WARNING, logger=rate_limit.logger.name):
            for _ in range(4):
                await buckets.hit(item, "k", fail_open=True)
        assert primary.calls == 1
        warnings = [r for r in caplog.records if r.name == rate_limit.logger.name]
        assert len(warnings) == 1
        assert "ConnectionError" in warnings[0].getMessage()

        now = rate_limit.time.monotonic()
        monkeypatch.setattr(
            rate_limit.time, "monotonic", lambda: now + rate_limit.STORAGE_RETRY_AFTER_SECONDS + 1
        )
        await buckets.hit(item, "k", fail_open=True)
        assert primary.calls == 2

    async def test_recovered_storage_resumes_and_is_logged(self, broken, caplog, monkeypatch):
        buckets, primary = broken
        item = parse("1/minute")
        await buckets.hit(item, "k", fail_open=True)
        assert buckets._dead_until is not None

        monkeypatch.setattr(buckets, "_primary", rate_limit.AsyncBuckets("memory://")._primary)
        now = rate_limit.time.monotonic()
        monkeypatch.setattr(
            rate_limit.time, "monotonic", lambda: now + rate_limit.STORAGE_RETRY_AFTER_SECONDS + 1
        )
        with caplog.at_level(logging.INFO, logger=rate_limit.logger.name):
            first = await buckets.hit(item, "k", fail_open=True)
            second = await buckets.hit(item, "k", fail_open=True)
        assert (first, second) == (True, False)
        assert buckets._dead_until is None
        assert any("recovered" in r.getMessage() for r in caplog.records)

    async def test_test_does_not_count(self):
        buckets = rate_limit.AsyncBuckets("memory://")
        item = parse("1/minute")
        assert [await buckets.test(item, "k", fail_open=False) for _ in range(3)] == [True] * 3
        assert await buckets.hit(item, "k", fail_open=False) is True
        assert await buckets.test(item, "k", fail_open=False) is False

    async def test_limiter_reset_clears_async_buckets(self, limiter):
        item = parse("1/minute")
        await limiter.buckets.hit(item, "k", fail_open=False)
        assert await limiter.buckets.test(item, "k", fail_open=False) is False
        limiter.reset()
        assert await limiter.buckets.test(item, "k", fail_open=False) is True

    async def test_unreachable_redis_fails_within_the_socket_timeout(self):
        import socket
        import time

        blackhole = socket.socket()
        blackhole.bind(("127.0.0.1", 0))
        blackhole.listen()
        try:
            buckets = rate_limit.AsyncBuckets(f"redis://127.0.0.1:{blackhole.getsockname()[1]}/0")
            started = time.perf_counter()
            assert await buckets.hit(parse("1/minute"), "k", fail_open=True) is True
            assert time.perf_counter() - started < 2
        finally:
            blackhole.close()

    async def test_login_failures_enforced_during_outage(
        self, limiter, isolated_db, monkeypatch
    ):
        monkeypatch.setattr(limiter.buckets, "_primary", _BrokenStorage())
        monkeypatch.setattr(rate_limit, "LOGIN_EMAIL_FAILURE_CEILING", parse("2/hour"))
        await _create_victim(isolated_db)
        async with _client(PROXY) as client:
            attack = [await _login(client, "192.0.2.1", WRONG_PASSWORD) for _ in range(2)]
            blocked = await _login(client, "192.0.2.1", VICTIM_PASSWORD)
            owner = await _login(client, "192.0.2.2", VICTIM_PASSWORD)
        assert attack == [401, 401]
        assert blocked == 429
        assert owner == 200


class TestRouteLimits:
    """Per-route limits replace the default and leave SSE streaming intact."""

    @staticmethod
    def _stream_app():
        from fastapi import APIRouter, Depends, FastAPI
        from fastapi.responses import StreamingResponse
        from septum_api.middleware.rate_limit import DefaultRateLimit

        router = APIRouter()

        @router.post("/api/chat/ask")
        @rate_limit.route_limit("2/minute", fail_open=True)
        async def ask():
            async def events():
                for i in range(3):
                    yield f"data: {i}\n\n"

            return StreamingResponse(events(), media_type="text/event-stream")

        stream_app = FastAPI()
        stream_app.include_router(router, dependencies=[Depends(DefaultRateLimit("600/minute"))])
        stream_app.add_exception_handler(rate_limit.RateLimited, rate_limit.rate_limited_handler)
        return stream_app

    async def test_own_limit_counts_once_and_streams_intact(self, limiter, monkeypatch):
        monkeypatch.setattr(rate_limit, "ROUTE_LIMITS", dict(rate_limit.ROUTE_LIMITS))
        transport = ASGITransport(app=self._stream_app(), client=("198.51.100.20", 1))
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            bodies = []
            for _ in range(2):
                async with client.stream("POST", "/api/chat/ask") as resp:
                    assert resp.status_code == 200
                    bodies.append("".join([chunk async for chunk in resp.aiter_text()]))
            third = await client.post("/api/chat/ask")
        assert bodies == ["data: 0\n\ndata: 1\n\ndata: 2\n\n"] * 2
        assert third.status_code == 429

    def test_registered_route_limits_and_outage_policies(self):
        from septum_api.routers import api_keys, auth, chat, error_logs, setup

        expected = {
            chat.chat_ask: ("30/minute", True),
            auth.login: ("5/minute", False),
            auth.register: ("3/minute", False),
            auth.change_password: ("5/minute", False),
            api_keys.create_api_key_endpoint: ("10/minute", True),
            error_logs.ingest_frontend_error: ("60/hour", False),
            setup.setup_status: ("120/minute", True),
        }
        site = {
            auth.login: parse("60/minute"),
            auth.register: parse("60/minute"),
            error_logs.ingest_frontend_error: parse("120/hour"),
        }
        for endpoint, (limit, fail_open) in expected.items():
            assert rate_limit.ROUTE_LIMITS[rate_limit._endpoint_name(endpoint)] == rate_limit.RouteLimit(
                parse(limit), fail_open, site.get(endpoint)
            )

    def test_only_probes_are_exempt(self):
        assert len(DEFAULT_LIMIT_EXEMPT_ENDPOINTS) == 2

    @pytest.mark.parametrize(
        ("path", "allowed", "peer"),
        [
            ("/api/chat/ask", 30, "198.51.100.31"),
            ("/api/auth/login", 5, "198.51.100.32"),
            ("/api/auth/register", 3, "198.51.100.33"),
            ("/api/auth/change-password", 5, "198.51.100.36"),
            ("/api/api-keys", 10, "198.51.100.34"),
            ("/api/error-logs/frontend", 60, "198.51.100.35"),
        ],
    )
    async def test_real_route_answers_429_past_its_own_limit(
        self, limiter, path, allowed, peer
    ):
        from septum_api.main import app as real_app

        async with AsyncClient(
            transport=ASGITransport(app=real_app, client=(peer, 40000)),
            base_url="http://test",
        ) as client:
            codes = [(await client.post(path, json={})).status_code for _ in range(allowed + 1)]
        assert 429 not in codes[:allowed]
        assert codes[allowed] == 429


class _GatedStorage:
    """Strategy stand-in whose calls wait on a gate, then fail."""

    def __init__(self) -> None:
        self.calls = 0
        self.gate = asyncio.Event()

    async def hit(self, *args, **kwargs):
        self.calls += 1
        await self.gate.wait()
        raise ConnectionError("still down")

    test = hit


class TestBreakerStates:
    @staticmethod
    def _open_breaker(buckets, monkeypatch) -> None:
        """Put *buckets* in the open state with the retry window already over."""
        now = rate_limit.time.monotonic()
        buckets._dead_until = now - 1
        buckets._last_warning = now
        monkeypatch.setattr(rate_limit.time, "monotonic", lambda: now)

    async def test_half_open_lets_exactly_one_probe_through(self, monkeypatch):
        buckets = rate_limit.AsyncBuckets("memory://")
        primary = _GatedStorage()
        monkeypatch.setattr(buckets, "_primary", primary)
        self._open_breaker(buckets, monkeypatch)
        item = parse("1/minute")

        probe = asyncio.create_task(buckets.hit(item, "k", fail_open=True))
        await asyncio.sleep(0)
        others = [await buckets.hit(item, "k", fail_open=True) for _ in range(5)]
        primary.gate.set()
        assert await probe is True
        assert others == [True] * 5
        assert primary.calls == 1
        assert not buckets.storage_up

    async def test_successful_probe_closes_the_breaker(self, monkeypatch):
        buckets = rate_limit.AsyncBuckets("memory://")
        self._open_breaker(buckets, monkeypatch)
        assert await buckets.hit(parse("1/minute"), "k", fail_open=False) is True
        assert buckets.storage_up

    async def test_warning_repeats_hourly_while_dead(self, monkeypatch, caplog):
        buckets = rate_limit.AsyncBuckets("memory://")
        monkeypatch.setattr(buckets, "_primary", _BrokenStorage())
        clock = [1000.0]
        monkeypatch.setattr(rate_limit.time, "monotonic", lambda: clock[0])
        item = parse("1/minute")
        with caplog.at_level(logging.WARNING, logger=rate_limit.logger.name):
            for _ in range(3):
                await buckets.hit(item, "k", fail_open=True)
                clock[0] += rate_limit.STORAGE_RETRY_AFTER_SECONDS + 1
            clock[0] += rate_limit.STORAGE_OUTAGE_REMINDER_SECONDS
            await buckets.hit(item, "k", fail_open=True)
        warnings = [r for r in caplog.records if r.name == rate_limit.logger.name]
        assert len(warnings) == 2

    async def test_breaker_state_is_exposed(self, limiter, monkeypatch):
        from prometheus_client import generate_latest

        monkeypatch.setattr(limiter.buckets, "_primary", _BrokenStorage())
        await limiter.buckets.hit(parse("1/minute"), "k", fail_open=True)
        assert b"septum_rate_limit_storage_up 0.0" in generate_latest()
        async with _client(UNTRUSTED_PEER) as client:
            health = (await client.get("/health")).json()
        assert health["rate_limit_storage"] == "degraded"


class TestFailureBucketIdentity:
    async def test_ipv6_clients_share_a_failure_bucket_per_48(self, limiter):
        wrong = {"X-Setup-Token": "wrong"}
        async with _client(PROXY) as client:
            codes = [
                (
                    await client.post(
                        "/api/setup/verify-token",
                        headers={**wrong, "X-Forwarded-For": f"2001:db8::{i + 1:x}"},
                    )
                ).status_code
                for i in range(10)
            ]
            same_prefix = await client.post(
                "/api/setup/verify-token", headers={**wrong, "X-Forwarded-For": "2001:db8::ffff"}
            )
            same_site = await client.post(
                "/api/setup/verify-token", headers={**wrong, "X-Forwarded-For": "2001:db8:0:1::1"}
            )
            other_prefix = await client.post(
                "/api/setup/verify-token", headers={**wrong, "X-Forwarded-For": "2001:db8:1::1"}
            )
        assert codes == [403] * 10
        assert same_prefix.status_code == 429
        assert same_site.status_code == 429
        assert other_prefix.status_code == 403

    async def test_correct_token_passes_after_the_bucket_is_spent(self, limiter, monkeypatch):
        from septum_api import bootstrap

        token = "a-correct-setup-token-0123456789abcdef"
        monkeypatch.setenv(bootstrap.SETUP_TOKEN_ENV, token)
        async with _client(UNTRUSTED_PEER) as client:
            spent = await _spend_verify_token(client)
            correct = await client.post("/api/setup/verify-token", headers={"X-Setup-Token": token})
        assert spent[-1] == 429
        assert correct.status_code == 204


class TestRound4:
    def test_ipv6_route_key_is_the_64(self):
        request = _fake_request(auth_method=None, auth_user_id=None)
        request.client = SimpleNamespace(host="2001:db8::1", port=1)
        same = _fake_request(auth_method=None, auth_user_id=None)
        same.client = SimpleNamespace(host="2001:db8::ffff:1", port=1)
        other = _fake_request(auth_method=None, auth_user_id=None)
        other.client = SimpleNamespace(host="2001:db8:0:1::1", port=1)
        assert get_rate_limit_key(request) == get_rate_limit_key(same)
        assert get_rate_limit_key(request) != get_rate_limit_key(other)
        assert get_rate_limit_key(request) == f"ip:{hash_text('2001:db8::/64')}"

    async def test_ipv6_rotation_inside_a_64_shares_the_route_bucket(self, tight_default_limit, isolated_db):
        async with _client(PROXY) as client:
            codes = [
                (await client.get("/api/auth/me", headers={"X-Forwarded-For": f"2001:db8::{i + 1:x}"})).status_code
                for i in range(3)
            ]
        assert codes[2] == 429

    async def test_retry_after_is_the_time_left_in_the_window(self, limiter, monkeypatch):
        item = parse("1/hour")
        clock = [1_000_000.0]
        monkeypatch.setattr(rate_limit.time, "time", lambda: clock[0])
        buckets = limiter.buckets
        await buckets.hit(item, "k", fail_open=False)
        stats = await buckets._primary.get_window_stats(item, "k")
        clock[0] = stats.reset_time - 600.4
        exc = await buckets.exceeded(item, "k")
        assert exc.headers["Retry-After"] == "601"

    async def test_429_carries_remaining_seconds(self, limiter, isolated_db, monkeypatch):
        monkeypatch.setattr(default_rate_limit, "item", parse("1/hour"))
        async with _client(UNTRUSTED_PEER) as client:
            await client.get("/api/auth/me")
            limited = await client.get("/api/auth/me")
        assert limited.status_code == 429
        assert 1 <= int(limited.headers["Retry-After"]) <= 3600


class TestFrontendErrorIntake:
    async def _post(self, session_maker, payload, headers=None):
        from septum_api.models.error_log import ErrorLog
        from sqlalchemy import select

        async with _client(UNTRUSTED_PEER) as client:
            resp = await client.post("/api/error-logs/frontend", json=payload, headers=headers or {})
        async with session_maker() as db:
            rows = (await db.execute(select(ErrorLog))).scalars().all()
        return resp, rows

    async def test_long_route_and_user_agent_are_cut_to_the_columns(self, limiter, isolated_db):
        resp, rows = await self._post(
            isolated_db,
            {"message": "boom", "route": "/r" * 1000},
            headers={"User-Agent": "u" * 2000},
        )
        assert resp.status_code == 204
        assert len(rows[0].path) == 512
        assert len(rows[0].user_agent) == 512

    @pytest.mark.parametrize(
        ("sent", "stored"),
        [("ERROR", "ERROR"), ("warning", "WARNING"), ("INFO", "INFO"), ("CRITICAL" * 5, "ERROR"), (None, "ERROR")],
    )
    async def test_level_is_restricted(self, limiter, isolated_db, sent, stored):
        resp, rows = await self._post(isolated_db, {"message": "boom", "level": sent})
        assert resp.status_code == 204
        assert rows[0].level == stored

    async def test_limit_holds_in_memory_during_an_outage(self, limiter, isolated_db, monkeypatch):
        monkeypatch.setattr(limiter.buckets, "_primary", _BrokenStorage())
        own = rate_limit.ROUTE_LIMITS[
            "septum_api.routers.error_logs.ingest_frontend_error"
        ]
        monkeypatch.setitem(
            rate_limit.ROUTE_LIMITS,
            "septum_api.routers.error_logs.ingest_frontend_error",
            rate_limit.RouteLimit(parse("2/hour"), own.fail_open),
        )
        async with _client(UNTRUSTED_PEER) as client:
            codes = [
                (await client.post("/api/error-logs/frontend", json={"message": "m"})).status_code
                for _ in range(3)
            ]
        assert codes == [204, 204, 429]


async def test_password_checks_run_off_the_event_loop(limiter, isolated_db, monkeypatch):
    import threading

    from septum_api.routers import auth as auth_router

    loop_thread = threading.get_ident()
    seen: list[int] = []
    real = auth_router.verify_password_or_dummy

    def recording(password, stored_hash):
        seen.append(threading.get_ident())
        return real(password, stored_hash)

    monkeypatch.setattr(auth_router, "verify_password_or_dummy", recording)
    await _create_victim(isolated_db)
    async with _client(PROXY) as client:
        assert await _login(client, "192.0.2.9", VICTIM_PASSWORD) == 200
    assert seen and all(ident != loop_thread for ident in seen)


async def test_setup_status_is_capped_at_its_own_limit(limiter, isolated_db, monkeypatch):
    from septum_api.routers import setup

    name = rate_limit._endpoint_name(setup.setup_status)
    monkeypatch.setitem(
        rate_limit.ROUTE_LIMITS, name, rate_limit.RouteLimit(parse("3/minute"), fail_open=True)
    )
    async with _client(UNTRUSTED_PEER) as client:
        codes = [(await client.get("/api/setup/status")).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


async def test_frontend_route_loses_query_and_fragment(limiter, isolated_db):
    from septum_api.models.error_log import ErrorLog
    from sqlalchemy import select

    async with _client(UNTRUSTED_PEER) as client:
        resp = await client.post(
            "/api/error-logs/frontend",
            json={"message": "m", "route": "/documents/7?token=secret&email=a@b.c#frag"},
        )
    async with isolated_db() as db:
        row = (await db.execute(select(ErrorLog))).scalar_one()
    assert resp.status_code == 204
    assert row.path == "/documents/7"


class TestIpv6SiteLimit:
    async def test_rotating_64s_inside_one_48_share_the_login_site_bucket(
        self, limiter, isolated_db, monkeypatch
    ):
        from septum_api.routers import auth as auth_router

        name = rate_limit._endpoint_name(auth_router.login)
        own = rate_limit.ROUTE_LIMITS[name]
        monkeypatch.setitem(
            rate_limit.ROUTE_LIMITS, name, rate_limit.RouteLimit(own.item, own.fail_open, parse("3/minute"))
        )
        async with _client(PROXY) as client:
            codes = [
                await _login(client, f"2001:db8:0:{i:x}::1", WRONG_PASSWORD) for i in range(4)
            ]
            other_site = await _login(client, "2001:db8:1::1", WRONG_PASSWORD)
            ipv4 = [await _login(client, f"192.0.2.{i + 1}", WRONG_PASSWORD) for i in range(4)]
        assert codes == [401, 401, 401, 429]
        assert other_site == 401
        assert 429 not in ipv4

    def test_site_digest_is_the_48_and_ipv4_has_none(self):
        request = _fake_request(auth_method=None, auth_user_id=None)
        request.client = SimpleNamespace(host="2001:db8:0:ffff::1", port=1)
        assert rate_limit.client_site_digest(request) == hash_text("2001:db8::/48")
        request.client = SimpleNamespace(host="192.0.2.1", port=1)
        assert rate_limit.client_site_digest(request) is None
        request.client = SimpleNamespace(host="::ffff:192.0.2.1", port=1)
        assert rate_limit.client_site_digest(request) is None


class TestIpv6FailureIdentity:
    async def test_login_marker_covers_the_whole_48(self, limiter, isolated_db, monkeypatch):
        monkeypatch.setattr(rate_limit, "LOGIN_EMAIL_FAILURE_CEILING", parse("2/hour"))
        await _create_victim(isolated_db)
        async with _client(PROXY) as client:
            spent = [await _login(client, "2001:db8:0:1::1", WRONG_PASSWORD) for _ in range(2)]
            rotated = await _login(client, "2001:db8:0:2::1", VICTIM_PASSWORD)
            other_site = await _login(client, "2001:db8:1::1", VICTIM_PASSWORD)
        assert spent == [401, 401]
        assert rotated == 429
        assert other_site == 200

    async def test_anonymous_error_log_budget_is_per_48(self, limiter, monkeypatch):
        monkeypatch.setattr(rate_limit, "ERROR_LOG_WRITE_LIMIT", parse("2/minute"))

        def anonymous(host: str):
            request = _fake_request(auth_method=None, auth_user_id=None)
            request.client = SimpleNamespace(host=host, port=1)
            return request

        results = [
            await rate_limit.error_log_write_allowed(anonymous(f"2001:db8:0:{i:x}::1"), server_fault=False)
            for i in range(3)
        ]
        other = await rate_limit.error_log_write_allowed(anonymous("2001:db8:1::1"), server_fault=False)
        assert results == [True, True, False]
        assert other is True

    async def test_frontend_intake_has_a_48_site_limit(self, limiter, isolated_db, monkeypatch):
        from septum_api.routers import error_logs

        name = rate_limit._endpoint_name(error_logs.ingest_frontend_error)
        own = rate_limit.ROUTE_LIMITS[name]
        monkeypatch.setitem(
            rate_limit.ROUTE_LIMITS, name, rate_limit.RouteLimit(own.item, own.fail_open, parse("2/hour"))
        )
        async with _client(PROXY) as client:
            codes = [
                (
                    await client.post(
                        "/api/error-logs/frontend",
                        json={"message": "m"},
                        headers={"X-Forwarded-For": f"2001:db8:0:{i:x}::1"},
                    )
                ).status_code
                for i in range(3)
            ]
        assert codes == [204, 204, 429]


class TestIpv4StaysPerAddress:
    async def test_setup_token_failures_do_not_cross_neighbouring_ipv4_addresses(self, limiter):
        wrong = {"X-Setup-Token": "wrong"}
        async with _client(PROXY) as client:
            spent = [
                (await client.post("/api/setup/verify-token", headers={**wrong, "X-Forwarded-For": "192.0.2.1"})).status_code
                for _ in range(VERIFY_TOKEN_LIMIT + 1)
            ]
            neighbour = await client.post(
                "/api/setup/verify-token", headers={**wrong, "X-Forwarded-For": "192.0.2.2"}
            )
        assert spent[-1] == 429
        assert neighbour.status_code == 403

    async def test_anonymous_error_log_budget_is_per_ipv4_address(self, limiter, monkeypatch):
        monkeypatch.setattr(rate_limit, "ERROR_LOG_WRITE_LIMIT", parse("2/minute"))

        def anonymous(host: str):
            request = _fake_request(auth_method=None, auth_user_id=None)
            request.client = SimpleNamespace(host=host, port=1)
            return request

        spent = [await rate_limit.error_log_write_allowed(anonymous("192.0.2.1"), server_fault=False) for _ in range(3)]
        neighbour = await rate_limit.error_log_write_allowed(anonymous("192.0.2.2"), server_fault=False)
        assert spent == [True, True, False]
        assert neighbour is True

    async def test_login_marker_does_not_cover_neighbouring_ipv4_addresses(
        self, limiter, isolated_db, monkeypatch
    ):
        monkeypatch.setattr(rate_limit, "LOGIN_EMAIL_FAILURE_CEILING", parse("2/hour"))
        await _create_victim(isolated_db)
        async with _client(PROXY) as client:
            spent = [await _login(client, "192.0.2.1", WRONG_PASSWORD) for _ in range(2)]
            same = await _login(client, "192.0.2.1", VICTIM_PASSWORD)
            neighbour = await _login(client, "192.0.2.2", VICTIM_PASSWORD)
        assert spent == [401, 401]
        assert same == 429
        assert neighbour == 200

    async def test_frontend_intake_site_limit_spares_other_48_and_ipv4(
        self, limiter, isolated_db, monkeypatch
    ):
        from septum_api.routers import error_logs

        name = rate_limit._endpoint_name(error_logs.ingest_frontend_error)
        own = rate_limit.ROUTE_LIMITS[name]
        monkeypatch.setitem(
            rate_limit.ROUTE_LIMITS, name, rate_limit.RouteLimit(own.item, own.fail_open, parse("2/hour"))
        )

        async def post(client: AsyncClient, address: str) -> int:
            resp = await client.post(
                "/api/error-logs/frontend", json={"message": "m"}, headers={"X-Forwarded-For": address}
            )
            return resp.status_code

        async with _client(PROXY) as client:
            site = [await post(client, f"2001:db8:0:{i:x}::1") for i in range(3)]
            other_site = await post(client, "2001:db8:1::1")
            ipv4 = [await post(client, "192.0.2.1") for _ in range(2)]
            ipv4_neighbour = await post(client, "192.0.2.2")
        assert site == [204, 204, 429]
        assert other_site == 204
        assert ipv4 == [204, 204]
        assert ipv4_neighbour == 204
