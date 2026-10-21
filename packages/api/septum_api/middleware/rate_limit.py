"""Rate limiting: identity key, default and per-route limits, failure buckets.

The key function picks one identity per request, in this order:

1. ``apikey:<prefix>`` for a validated API key, so services sharing a
   host each get their own quota;
2. ``user:<id>`` for a validated JWT, so users behind one address do not
   share a bucket;
3. ``ip:<HMAC of address>`` otherwise, the client address uvicorn
   resolved from ``X-Forwarded-For`` only when the peer is a trusted
   proxy; an IPv6 client counts as its /64 (:func:`client_network_digest`).
   Hashed so limiter storage never holds a raw address.

Failure buckets (login marker, setup-token failures, anonymous error-log
budget) count IPv6 per /48 and IPv4 per address (:func:`client_failure_digest`).

``AuthMiddleware`` sets the identity before routing, so
:class:`DefaultRateLimit` (a router dependency) sees it.

Every counter is async (:class:`AsyncBuckets`): a slow or dead Redis never
blocks the event loop. Each limit says what happens during a storage
outage: general limits fail open, brute-force controls (login, register,
setup token) keep counting in process memory.
"""

from __future__ import annotations

import ipaddress
import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypeVar

from fastapi import HTTPException, Request
from limits import RateLimitItem, parse
from limits.aio.storage import MemoryStorage
from limits.aio.strategies import FixedWindowRateLimiter
from limits.storage import storage_from_string
from prometheus_client import Gauge
from starlette.responses import JSONResponse

from ..bootstrap import BootstrapConfig, get_config
from ..services.api_key_service import (
    HEADER_NAME as _API_KEY_HEADER,
)
from ..services.api_key_service import (
    PREFIX as _API_KEY_PREFIX,
)
from ..utils.client_ip import resolve_client_ip
from ..utils.crypto import hash_text

logger = logging.getLogger(__name__)

_limiter: RateLimiter | None = None

_API_KEY_HEADER_LOWER = _API_KEY_HEADER.lower()
_API_KEY_PREFIX_OFFSET = len(_API_KEY_PREFIX)
_API_KEY_PREFIX_END = _API_KEY_PREFIX_OFFSET + 8

# Only the health and metrics probes stay outside every limit, matched by
# endpoint (not path) so a mount prefix or --root-path cannot change it.
# Both are app-level routes the router dependency never sees; they are
# listed so the set is the whole exemption policy. /api/setup/status runs
# a database query, so it has a generous limit of its own instead.
DEFAULT_LIMIT_EXEMPT_ENDPOINTS = frozenset(
    {
        "septum_api.main.health",
        "septum_api.utils.metrics.metrics_endpoint",
    }
)

# Failed logins only. The email ceiling blocks just the clients that
# themselves failed for that email within the marker window, so a clean
# client with the right password always gets in.
LOGIN_EMAIL_FAILURE_CEILING = parse("100/hour")
LOGIN_CLIENT_FAILURE_MARKER = parse("1/hour")
_LOGIN_EMAIL_CEILING_NAMESPACE = "login-email-fail"
_LOGIN_CLIENT_MARKER_NAMESPACE = "login-client-fail"

# Failed setup-token checks per client, shared by every gated route.
SETUP_TOKEN_FAILURE_LIMIT = parse("10/minute")
_SETUP_TOKEN_FAILURE_NAMESPACE = "setup-token-fail"

# Error log rows any one identity may cause, across all routes: one budget
# for client errors and other noise, a separate one for server faults.
ERROR_LOG_WRITE_LIMIT = parse("60/minute")
ERROR_LOG_FAULT_WRITE_LIMIT = parse("20/minute")
_ERROR_LOG_WRITE_NAMESPACE = "error-log-write"
_ERROR_LOG_FAULT_WRITE_NAMESPACE = "error-log-fault-write"

# Redis must answer fast or not at all. A dead store is probed again after
# the retry window; while it stays dead the warning repeats hourly.
STORAGE_SOCKET_TIMEOUT_SECONDS = 0.5
STORAGE_RETRY_AFTER_SECONDS = 30.0
STORAGE_OUTAGE_REMINDER_SECONDS = 3600.0

# IPv6: route and default buckets count per /64 (one host's prefix);
# failure buckets and the optional site limits count per /48 (one site's).
_IPV6_CLIENT_PREFIX = 64
_IPV6_SITE_PREFIX = 48


class RateLimited(HTTPException):
    """429 raised by every limit in this module."""

    def __init__(self, item: RateLimitItem, retry_after: int | None = None) -> None:
        seconds = item.get_expiry() if retry_after is None else retry_after
        super().__init__(
            status_code=429,
            detail=str(item),
            headers={"Retry-After": str(seconds)},
        )


async def rate_limited_handler(request: Request, exc: RateLimited) -> JSONResponse:
    """Answer a :class:`RateLimited` without writing an error log row."""
    return JSONResponse(
        {"error": f"Rate limit exceeded: {exc.detail}"},
        status_code=exc.status_code,
        headers=exc.headers,
    )


def _redis_options() -> dict[str, Any]:
    return {
        "socket_timeout": STORAGE_SOCKET_TIMEOUT_SECONDS,
        "socket_connect_timeout": STORAGE_SOCKET_TIMEOUT_SECONDS,
    }


class AsyncBuckets:
    """Async counters behind a circuit breaker.

    Closed: every call goes to the storage. A failure opens the breaker
    for ``STORAGE_RETRY_AFTER_SECONDS`` and logs a warning (class name
    only), repeated hourly while the outage lasts. Open: a ``fail_open``
    check passes and any other check runs against an in-process fallback.
    Half-open: once the window has passed, exactly one call probes the
    storage while the rest keep the open path; success closes the breaker,
    failure opens it again.
    """

    def __init__(self, storage_uri: str) -> None:
        if storage_uri.startswith(("redis://", "rediss://")):
            storage = storage_from_string(
                f"async+{storage_uri}", implementation="redispy", **_redis_options()
            )
        else:
            storage = MemoryStorage()
        self._primary = FixedWindowRateLimiter(storage)
        self._fallback = FixedWindowRateLimiter(MemoryStorage())
        self._dead_until: float | None = None
        self._probing = False
        self._last_warning = 0.0

    @property
    def storage_up(self) -> bool:
        """Whether the breaker is closed (storage answering)."""
        return self._dead_until is None

    async def hit(self, item: RateLimitItem, *identifiers: str, fail_open: bool) -> bool:
        """Count one hit; return False when the limit is already spent."""
        return await self._run(lambda strategy: strategy.hit(item, *identifiers), fail_open)

    async def test(self, item: RateLimitItem, *identifiers: str, fail_open: bool) -> bool:
        """Return whether one more hit would be allowed, without counting."""
        return await self._run(lambda strategy: strategy.test(item, *identifiers), fail_open)

    async def retry_after(self, item: RateLimitItem, *identifiers: str) -> int:
        """Seconds until the window of a spent bucket resets (at least 1).

        Reads only the key's expiry (one TTL lookup on Redis), not the
        full window statistics.
        """
        key = item.key_for(*identifiers)
        reset_at = await self._run(lambda strategy: strategy.storage.get_expiry(key), False)
        return max(1, math.ceil(reset_at - time.time()))

    async def exceeded(self, item: RateLimitItem, *identifiers: str) -> RateLimited:
        """Build the 429 for a spent bucket, with the time left in its window."""
        return RateLimited(item, await self.retry_after(item, *identifiers))

    async def _run(
        self, operation: Callable[[FixedWindowRateLimiter], Awaitable[Any]], fail_open: bool
    ) -> Any:
        if self._may_use_primary():
            probing = self._dead_until is not None
            try:
                result = await operation(self._primary)
            except Exception as exc:
                self._open(exc)
            else:
                if probing:
                    self._dead_until = None
                    logger.info("Rate-limit storage recovered")
                return result
            finally:
                if probing:
                    self._probing = False
        if fail_open:
            return True
        return await operation(self._fallback)

    def _may_use_primary(self) -> bool:
        if self._dead_until is None:
            return True
        if self._probing or time.monotonic() < self._dead_until:
            return False
        self._probing = True
        return True

    def _open(self, exc: Exception) -> None:
        now = time.monotonic()
        if self._dead_until is None or now - self._last_warning >= STORAGE_OUTAGE_REMINDER_SECONDS:
            logger.warning(
                "Rate-limit storage unavailable (%s): general limits open, "
                "brute-force limits held in memory; retrying every %ss",
                type(exc).__name__,
                int(STORAGE_RETRY_AFTER_SECONDS),
            )
            self._last_warning = now
        self._dead_until = now + STORAGE_RETRY_AFTER_SECONDS


class RateLimiter:
    """Process-wide limiter state: the switch tests flip and the buckets."""

    def __init__(self, storage_uri: str) -> None:
        self.enabled = True
        self._storage_uri = storage_uri
        self.buckets = AsyncBuckets(storage_uri)

    def reset(self) -> None:
        """Start the buckets afresh. With Redis, existing keys expire with their window."""
        self.buckets = AsyncBuckets(self._storage_uri)


@dataclass(frozen=True)
class RouteLimit:
    """A route's own limit, replacing the default; ``fail_open`` is its outage policy.

    ``site_item`` adds a looser bucket per IPv6 /48 (see :func:`client_site_digest`).
    """

    item: RateLimitItem
    fail_open: bool
    site_item: RateLimitItem | None = None


# Filled by :func:`route_limit`; keyed on the endpoint's qualified name, so
# a mount prefix or ``--root-path`` cannot change which limit applies.
ROUTE_LIMITS: dict[str, RouteLimit] = {}

_Endpoint = TypeVar("_Endpoint", bound=Callable[..., Any])


def _endpoint_name(endpoint: Callable[..., Any]) -> str:
    return f"{endpoint.__module__}.{endpoint.__qualname__}"


def route_limit(
    limit: str, *, fail_open: bool, ipv6_site_limit: str | None = None
) -> Callable[[_Endpoint], _Endpoint]:
    """Give an endpoint its own limit instead of the default (the endpoint is not wrapped).

    Enforced by :class:`DefaultRateLimit` when the request starts, before
    any route dependency or body, so rejected requests are counted too.
    *ipv6_site_limit* also caps all IPv6 clients of one /48 together.
    """

    def register(endpoint: _Endpoint) -> _Endpoint:
        site_item = parse(ipv6_site_limit) if ipv6_site_limit else None
        ROUTE_LIMITS[_endpoint_name(endpoint)] = RouteLimit(parse(limit), fail_open, site_item)
        return endpoint

    return register


def get_rate_limit_key(request: Request) -> str:
    """Return the rate-limit bucket for *request*: API key, then user, then client address."""
    state = getattr(request, "state", None)
    auth_method = getattr(state, "auth_method", None)
    if auth_method == "api_key":
        api_key_header = request.headers.get(_API_KEY_HEADER_LOWER, "")
        prefix = (
            api_key_header[_API_KEY_PREFIX_OFFSET:_API_KEY_PREFIX_END]
            if len(api_key_header) >= _API_KEY_PREFIX_END
            else "unknown"
        )
        return f"apikey:{prefix}"
    user_id = getattr(state, "auth_user_id", None)
    if user_id is not None:
        return f"user:{user_id}"
    return _anonymous_key(client_network_digest(request))


_ANONYMOUS_KEY_PREFIX = "ip:"


def _anonymous_key(client_digest: str) -> str:
    return f"{_ANONYMOUS_KEY_PREFIX}{client_digest}"


def is_anonymous_key(key: str) -> bool:
    """Whether a :func:`get_rate_limit_key` result identifies a caller only by address."""
    return key.startswith(_ANONYMOUS_KEY_PREFIX)


_ClientAddress = ipaddress.IPv4Address | ipaddress.IPv6Address | str | None


def _parsed_client(request: Request) -> _ClientAddress:
    """Resolve the client once: an IP address object, the raw host if it is not an IP, or None."""
    address = resolve_client_ip(request)
    if not address:
        return None
    try:
        return ipaddress.ip_address(address)
    except ValueError:
        return address


def _network_digest(client: _ClientAddress) -> str:
    if client is None:
        return "unknown"
    if isinstance(client, ipaddress.IPv6Address):
        return hash_text(str(ipaddress.ip_network(f"{client}/{_IPV6_CLIENT_PREFIX}", strict=False)))
    return hash_text(str(client))


def _site_digest(client: _ClientAddress) -> str | None:
    if not isinstance(client, ipaddress.IPv6Address) or client.ipv4_mapped is not None:
        return None
    return hash_text(str(ipaddress.ip_network(f"{client}/{_IPV6_SITE_PREFIX}", strict=False)))


def client_network_digest(request: Request) -> str:
    """Return an HMAC of the client's identity for every address-keyed bucket.

    IPv4 clients are their address; IPv6 clients are their /64, since one
    host usually controls the whole prefix and could rotate through it.
    ``unknown`` without a peer.
    """
    return _network_digest(_parsed_client(request))


def client_site_digest(request: Request) -> str | None:
    """Return an HMAC of an IPv6 client's /48, or None for IPv4 and unknown peers.

    A /48 is what one site is usually given, so a single network can hold
    65 536 /64 "clients"; the site bucket stops that from multiplying a
    per-client limit. IPv4 has no equivalent: one address is one client.
    """
    return _site_digest(_parsed_client(request))


def client_failure_digest(request: Request) -> str:
    """Identity for failure buckets: an IPv6 client's /48, else its /64 or IPv4 address.

    Failure buckets (login marker, setup-token failures, anonymous error-log
    budget) must not reset when a client moves to another /64 of its site.
    """
    client = _parsed_client(request)
    return _site_digest(client) or _network_digest(client)


class DefaultRateLimit:
    """Router dependency applying a limit per route and identity.

    A route registered with :func:`route_limit` gets its own limit and
    outage policy; every other route gets the default, which fails open;
    probes are exempt. Router dependencies run before the route's own and
    before a streaming body starts, so each request is counted once,
    rejected ones included.
    """

    def __init__(self, limit: str) -> None:
        self.item = parse(limit)

    async def __call__(self, request: Request) -> None:
        limiter = get_limiter()
        endpoint = request.scope.get("endpoint")
        if not limiter.enabled or endpoint is None:
            return
        name = _endpoint_name(endpoint)
        own = ROUTE_LIMITS.get(name)
        if own is None and name in DEFAULT_LIMIT_EXEMPT_ENDPOINTS:
            return
        item, fail_open = (own.item, own.fail_open) if own else (self.item, True)
        identifiers = ("route", name, get_rate_limit_key(request))
        if not await limiter.buckets.hit(item, *identifiers, fail_open=fail_open):
            raise await limiter.buckets.exceeded(item, *identifiers)
        site = client_site_digest(request) if own and own.site_item else None
        if site is not None:
            site_identifiers = ("route-site", name, site)
            if not await limiter.buckets.hit(own.site_item, *site_identifiers, fail_open=fail_open):
                raise await limiter.buckets.exceeded(own.site_item, *site_identifiers)


def _login_identifiers(request: Request, email: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the (email ceiling, client marker) bucket identifiers for *email*."""
    email_digest = hash_text(email.strip().lower())
    return (
        (_LOGIN_EMAIL_CEILING_NAMESPACE, email_digest),
        (_LOGIN_CLIENT_MARKER_NAMESPACE, email_digest, client_failure_digest(request)),
    )


async def check_login_failures(request: Request, email: str) -> None:
    """429 when *email* is over its failure ceiling and this client is one of the failers.

    Counts nothing; only :func:`record_login_failure` does, so successful
    logins are free. A client with no failure for this email in the last
    marker window always gets through, so an attacker who spends the
    ceiling cannot lock the owner out. Per-client guessing stays bounded
    by the login route's own per-client limit; once the ceiling is spent
    every further failing client adds at most one guess per marker
    window. Keys are HMACs, so the storage holds neither email nor address.
    Enforced from an in-process fallback while the storage is down.
    """
    limiter = get_limiter()
    if not limiter.enabled:
        return
    ceiling, marker = _login_identifiers(request, email)
    buckets = limiter.buckets
    if not await buckets.test(LOGIN_EMAIL_FAILURE_CEILING, *ceiling, fail_open=False) and not (
        await buckets.test(LOGIN_CLIENT_FAILURE_MARKER, *marker, fail_open=False)
    ):
        # Either window ending lets this client in again.
        retry_after = min(
            await buckets.retry_after(LOGIN_EMAIL_FAILURE_CEILING, *ceiling),
            await buckets.retry_after(LOGIN_CLIENT_FAILURE_MARKER, *marker),
        )
        raise RateLimited(LOGIN_EMAIL_FAILURE_CEILING, retry_after)


async def record_login_failure(request: Request, email: str) -> None:
    """Count one failed login for *email* and mark this client as a failer."""
    limiter = get_limiter()
    if not limiter.enabled:
        return
    ceiling, marker = _login_identifiers(request, email)
    await limiter.buckets.hit(LOGIN_EMAIL_FAILURE_CEILING, *ceiling, fail_open=False)
    await limiter.buckets.hit(LOGIN_CLIENT_FAILURE_MARKER, *marker, fail_open=False)


async def check_setup_token_failures(request: Request) -> None:
    """429 once this client has used up its failed setup-token attempts; counts nothing."""
    limiter = get_limiter()
    if not limiter.enabled:
        return
    identifiers = (_SETUP_TOKEN_FAILURE_NAMESPACE, client_failure_digest(request))
    if not await limiter.buckets.test(SETUP_TOKEN_FAILURE_LIMIT, *identifiers, fail_open=False):
        raise await limiter.buckets.exceeded(SETUP_TOKEN_FAILURE_LIMIT, *identifiers)


async def record_setup_token_failure(request: Request) -> None:
    """Count one failed setup-token check for this client."""
    limiter = get_limiter()
    if limiter.enabled:
        await limiter.buckets.hit(
            SETUP_TOKEN_FAILURE_LIMIT,
            _SETUP_TOKEN_FAILURE_NAMESPACE,
            client_failure_digest(request),
            fail_open=False,
        )


async def error_log_write_allowed(
    request: Request | None, *, server_fault: bool, identity: str | None = None
) -> bool:
    """Count one error log row for a caller; False once its budget is spent.

    The caller is *identity* when given (background work on a user's
    behalf, ``user:<id>``), else the request's rate-limit key. Server
    faults (5xx) and everything else have separate budgets. Enforced from
    memory while the storage is down, so an outage cannot become a way to
    flood the error log.
    """
    limiter = get_limiter()
    if not limiter.enabled:
        return True
    if server_fault:
        item, namespace = ERROR_LOG_FAULT_WRITE_LIMIT, _ERROR_LOG_FAULT_WRITE_NAMESPACE
    else:
        item, namespace = ERROR_LOG_WRITE_LIMIT, _ERROR_LOG_WRITE_NAMESPACE
    if identity is not None:
        key = identity
    else:
        key = get_rate_limit_key(request)
        if is_anonymous_key(key):
            # Anonymous IPv6 callers share one budget per /48.
            key = _anonymous_key(client_failure_digest(request))
    return await limiter.buckets.hit(item, namespace, key, fail_open=False)


def create_limiter(config: BootstrapConfig) -> RateLimiter:
    """Build the limiter on the configured Redis, or in memory without one.

    The URL is passed through unchanged, so a TLS-required ``rediss://``
    is not downgraded.
    """
    return RateLimiter(config.redis_url or "memory://")


def get_limiter() -> RateLimiter:
    """Return the shared limiter (created on first call, from the bootstrap config)."""
    global _limiter  # noqa: PLW0603
    if _limiter is None:
        _limiter = create_limiter(get_config())

    return _limiter

STORAGE_UP = Gauge(
    "septum_rate_limit_storage_up",
    "1 while the rate-limit storage answers, 0 while its circuit breaker is open",
)
STORAGE_UP.set_function(lambda: 1.0 if get_limiter().buckets.storage_up else 0.0)
