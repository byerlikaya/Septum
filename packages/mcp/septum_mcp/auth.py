"""ASGI auth middleware for the septum-mcp HTTP transport.

The bundled ``mcp`` SDK ships an OAuth2 scaffold for the HTTP
transports, but that targets full issuer / client-registration
deployments. A single-tenant septum-mcp instance behind a reverse
proxy only needs a static bearer token check, which is what this
module provides.

When ``token`` is ``None`` the middleware is a no-op — intended for
localhost-only development. In every other deployment the deploy
surface should set ``SEPTUM_MCP_HTTP_TOKEN`` (or pass ``--token``).

The middleware also answers ``GET /health`` with a 200 OK JSON
payload so Docker ``HEALTHCHECK`` directives and reverse-proxy
probes can hit a stable endpoint that skips auth.
"""

from __future__ import annotations

import hmac
import json

_HEALTH_PATH = "/health"


class BearerTokenMiddleware:
    """ASGI middleware that gates HTTP requests behind a static bearer token.

    Non-HTTP scopes (``lifespan``, ``websocket``) pass through unchanged.
    The ``/health`` path is always served with 200 OK and never requires
    auth so reverse proxies and container healthchecks work unconditionally.
    """

    def __init__(self, app, *, token: str | None) -> None:
        self._app = app
        self._token = token

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path == _HEALTH_PATH:
            await _send_health_ok(send)
            return

        if self._token is None:
            await self._app(scope, receive, send)
            return

        header_value = _header(scope, b"authorization")
        expected = f"Bearer {self._token}".encode()
        if header_value is None or not hmac.compare_digest(header_value, expected):
            await _send_401(send)
            return

        await self._app(scope, receive, send)


def _header(scope, name: bytes) -> bytes | None:  # type: ignore[no-untyped-def]
    name = name.lower()
    for header_name, value in scope.get("headers", []):
        if header_name.lower() == name:
            return value
    return None


async def _send_health_ok(send) -> None:  # type: ignore[no-untyped-def]
    body = json.dumps({"status": "ok"}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"cache-control", b"no-store"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _send_401(send) -> None:  # type: ignore[no-untyped-def]
    body = json.dumps({"error": "unauthorized"}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b'Bearer realm="septum-mcp"'),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
