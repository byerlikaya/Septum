"""ASGI auth middleware tests for septum_mcp.auth.BearerTokenMiddleware."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Dict, List, Tuple

import pytest

from septum_mcp.auth import BearerTokenMiddleware


async def _dummy_app(scope: Dict[str, Any], receive: Callable[[], Awaitable[Dict[str, Any]]], send: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
    """Test ASGI app that echoes scope details in a 200 body."""
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": b"downstream-ok"})


class _RecordingSend:
    """Collect messages emitted by the middleware for assertions."""

    def __init__(self) -> None:
        self.messages: List[Dict[str, Any]] = []

    async def __call__(self, message: Dict[str, Any]) -> None:
        self.messages.append(message)

    @property
    def status(self) -> int:
        for m in self.messages:
            if m.get("type") == "http.response.start":
                return int(m["status"])
        raise AssertionError("no response.start emitted")

    @property
    def body(self) -> bytes:
        parts: List[bytes] = []
        for m in self.messages:
            if m.get("type") == "http.response.body":
                parts.append(m.get("body", b""))
        return b"".join(parts)

    def header(self, name: bytes) -> bytes | None:
        for m in self.messages:
            if m.get("type") == "http.response.start":
                for k, v in m.get("headers", []):
                    if k.lower() == name.lower():
                        return v
        return None


def _http_scope(path: str = "/mcp", auth_header: str | None = None) -> Dict[str, Any]:
    headers: List[Tuple[bytes, bytes]] = []
    if auth_header is not None:
        headers.append((b"authorization", auth_header.encode()))
    return {"type": "http", "path": path, "headers": headers}


async def _noop_receive() -> Dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


@pytest.mark.asyncio
async def test_no_token_passes_through_to_downstream() -> None:
    """When token is None the middleware is a no-op (local dev path)."""
    mw = BearerTokenMiddleware(_dummy_app, token=None)
    send = _RecordingSend()

    await mw(_http_scope(auth_header=None), _noop_receive, send)

    assert send.status == 200
    assert send.body == b"downstream-ok"


@pytest.mark.asyncio
async def test_matching_token_passes_through() -> None:
    mw = BearerTokenMiddleware(_dummy_app, token="secret-123")
    send = _RecordingSend()

    await mw(_http_scope(auth_header="Bearer secret-123"), _noop_receive, send)

    assert send.status == 200
    assert send.body == b"downstream-ok"


@pytest.mark.asyncio
async def test_missing_authorization_returns_401() -> None:
    mw = BearerTokenMiddleware(_dummy_app, token="secret-123")
    send = _RecordingSend()

    await mw(_http_scope(auth_header=None), _noop_receive, send)

    assert send.status == 401
    assert send.header(b"www-authenticate") == b'Bearer realm="septum-mcp"'
    payload = json.loads(send.body.decode())
    assert payload == {"error": "unauthorized"}


@pytest.mark.asyncio
async def test_wrong_token_returns_401() -> None:
    mw = BearerTokenMiddleware(_dummy_app, token="secret-123")
    send = _RecordingSend()

    await mw(_http_scope(auth_header="Bearer wrong"), _noop_receive, send)

    assert send.status == 401


@pytest.mark.asyncio
async def test_non_bearer_scheme_returns_401() -> None:
    mw = BearerTokenMiddleware(_dummy_app, token="secret-123")
    send = _RecordingSend()

    await mw(_http_scope(auth_header="Basic dXNlcjpwYXNz"), _noop_receive, send)

    assert send.status == 401


@pytest.mark.asyncio
async def test_health_endpoint_bypasses_auth() -> None:
    """/health must answer 200 even when a token is configured."""
    mw = BearerTokenMiddleware(_dummy_app, token="secret-123")
    send = _RecordingSend()

    await mw(_http_scope(path="/health", auth_header=None), _noop_receive, send)

    assert send.status == 200
    payload = json.loads(send.body.decode())
    assert payload == {"status": "ok"}


@pytest.mark.asyncio
async def test_health_endpoint_does_not_hit_downstream() -> None:
    """Health must be answered by the middleware, not the wrapped MCP app."""
    called = False

    async def _tripwire(scope, receive, send):
        nonlocal called
        called = True

    mw = BearerTokenMiddleware(_tripwire, token=None)
    send = _RecordingSend()

    await mw(_http_scope(path="/health", auth_header=None), _noop_receive, send)

    assert called is False
    assert send.status == 200


@pytest.mark.asyncio
async def test_non_http_scope_passes_through() -> None:
    """lifespan / websocket / other scopes must not be gated."""
    received_types: List[str] = []

    async def _tripwire(scope, receive, send):
        received_types.append(scope["type"])

    mw = BearerTokenMiddleware(_tripwire, token="secret-123")

    await mw({"type": "lifespan"}, _noop_receive, _RecordingSend())
    await mw({"type": "websocket", "path": "/ws"}, _noop_receive, _RecordingSend())

    assert received_types == ["lifespan", "websocket"]
