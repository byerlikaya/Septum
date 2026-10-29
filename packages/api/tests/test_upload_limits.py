"""Uploads stay in RAM (never spooled to a temp file) and are bounded in size, time and concurrency."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List, Tuple

import pytest
from disk_write_guard import install_write_guard
from fastapi import FastAPI, File, UploadFile
from httpx import ASGITransport, AsyncClient
from starlette.formparsers import MultiPartParser
from starlette.types import ASGIApp, Receive, Scope, Send

from septum_api.middleware import upload_limits
from septum_api.middleware.auth import AuthMiddleware
from septum_api.middleware.upload_limits import (
    UPLOAD_PATH,
    UploadLimitMiddleware,
    keep_multipart_uploads_in_memory,
)
from septum_api.services.auth import create_access_token

_AUTHED = {"Authorization": "Bearer valid"}
_FILES = {"file": ("a.txt", b"hello", "text/plain")}


class _FakeAuth:
    """Stands in for AuthMiddleware: ``Bearer valid`` resolves to user 1, anything else to nobody."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            state = scope.setdefault("state", {})
            state["auth_user_id"] = 1 if (b"authorization", b"Bearer valid") in scope["headers"] else None
        await self.app(scope, receive, send)


def _stack(
    gate: asyncio.Event | None = None,
    entered: asyncio.Event | None = None,
    fail: bool = False,
) -> Tuple[ASGIApp, UploadLimitMiddleware]:
    """An upload route (held open on ``gate``) plus a JSON route, behind the limit and a fake auth."""

    app = FastAPI()

    @app.post(UPLOAD_PATH)
    async def upload(file: UploadFile = File(...)) -> dict:
        size = len(await file.read())
        if entered is not None:
            entered.set()
        if gate is not None:
            await gate.wait()
        if fail:
            raise RuntimeError("route failed")
        return {"size": size}

    @app.post("/api/other")
    async def other() -> dict:
        return {"ok": True}

    @app.post("/api/other-form")
    async def other_form(file: UploadFile = File(...)) -> dict:
        return {"size": len(await file.read())}

    limit = UploadLimitMiddleware(app)
    return _FakeAuth(limit), limit


def _client(app: ASGIApp, headers: Dict[str, str] | None = None, **transport: Any) -> AsyncClient:
    """Client that authenticates unless ``headers`` overrides it."""
    return AsyncClient(
        transport=ASGITransport(app=app, **transport),
        base_url="http://test",
        headers=_AUTHED if headers is None else headers,
    )


async def _call(app: ASGIApp, scope_extra: Dict[str, Any], messages: List[dict]) -> Tuple[List[dict], List[int]]:
    """Drive ``app`` with raw ASGI messages; return what it sent and the slot count at response start."""

    scope = {
        "type": "http",
        "method": "POST",
        "path": UPLOAD_PATH,
        "raw_path": UPLOAD_PATH.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"content-type", b"multipart/form-data; boundary=b")],
        "state": {"auth_user_id": 1},
        **scope_extra,
    }
    pending = list(messages)
    sent: List[dict] = []
    active_at_start: List[int] = []
    limit = app

    async def receive() -> dict:
        if pending:
            return pending.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        if message["type"] == "http.response.start":
            active_at_start.append(limit.active)
        sent.append(message)

    try:
        await app(scope, receive, send)
    except Exception:
        sent.append({"type": "raised"})
    return sent, active_at_start


def _status(sent: List[dict]) -> int | None:
    return next((m["status"] for m in sent if m["type"] == "http.response.start"), None)


def test_app_keeps_uploads_in_memory_and_installs_the_limit_inside_auth() -> None:
    """The real app raises the spool limit to the body cap and runs the limit inside AuthMiddleware."""

    from septum_api.main import app

    classes = [m.cls for m in app.user_middleware]
    assert MultiPartParser.spool_max_size == upload_limits.MAX_UPLOAD_BODY_BYTES
    assert classes.index(UploadLimitMiddleware) > classes.index(AuthMiddleware)


def test_defaults() -> None:
    """One upload per worker and a 30-second read-idle bound are the documented defaults."""

    assert upload_limits.MAX_CONCURRENT_UPLOADS == 1
    assert upload_limits.UPLOAD_READ_IDLE_TIMEOUT_SECONDS == 30.0


@pytest.mark.asyncio
async def test_large_multipart_upload_creates_no_temp_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """A file part well above Starlette's 1 MB default spool size never rolls over to disk."""

    monkeypatch.setattr(MultiPartParser, "spool_max_size", MultiPartParser.spool_max_size)
    keep_multipart_uploads_in_memory()
    payload = b"x" * (3 * 1024 * 1024)
    app, _ = _stack()

    async with _client(app) as client:
        attempts = install_write_guard(monkeypatch)
        response = await client.post(UPLOAD_PATH, files={"file": ("scan.bin", payload, "application/octet-stream")})

    assert response.status_code == 200
    assert response.json() == {"size": len(payload)}
    assert attempts == []


@pytest.mark.asyncio
async def test_default_spool_size_would_write_a_temp_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Control for the test above: with the 1 MB default the guard does see the rollover."""

    monkeypatch.setattr(MultiPartParser, "spool_max_size", 1024 * 1024)
    app, _ = _stack()

    async with _client(app) as client:
        attempts = install_write_guard(monkeypatch)
        response = await client.post(UPLOAD_PATH, files={"file": ("scan.bin", b"x" * (3 * 1024 * 1024), "application/octet-stream")})

    assert response.status_code == 200
    assert attempts != []


@pytest.mark.asyncio
async def test_concurrent_uploads_beyond_the_limit_get_503() -> None:
    """While the only slot is taken a second upload is refused; once answered, the slot frees."""

    gate, entered = asyncio.Event(), asyncio.Event()
    app, limit = _stack(gate, entered)

    async with _client(app) as client:
        first = asyncio.create_task(client.post(UPLOAD_PATH, files=_FILES))
        await asyncio.wait_for(entered.wait(), timeout=5)

        refused = await asyncio.wait_for(client.post(UPLOAD_PATH, files=_FILES), 5)
        other_route = await asyncio.wait_for(client.post("/api/other"), 5)

        gate.set()
        accepted = await asyncio.wait_for(first, 5)
        after = await asyncio.wait_for(client.post(UPLOAD_PATH, files=_FILES), 5)

    assert refused.status_code == 503
    assert refused.headers["Retry-After"] == str(upload_limits.UPLOAD_BUSY_RETRY_AFTER_SECONDS)
    assert refused.json()["code"] == "server_busy"
    assert other_route.status_code == 200
    assert accepted.status_code == 200
    assert after.status_code == 200
    assert limit.active == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer invalid"}, {"X-API-Key": "sk-unknown"}])
async def test_unauthenticated_upload_gets_401_without_a_slot(headers: Dict[str, str]) -> None:
    """A caller AuthMiddleware did not resolve is refused before the body is read."""

    app, limit = _stack()
    async with _client(app, headers) as anonymous:
        refusals = [await anonymous.post(UPLOAD_PATH, files=_FILES) for _ in range(3)]
    async with _client(app) as client:
        accepted = await client.post(UPLOAD_PATH, files=_FILES)

    assert [r.status_code for r in refusals] == [401, 401, 401]
    assert refusals[0].json() == {"detail": "Not authenticated"}
    assert accepted.status_code == 200

    sent, active_at_start = await _call(limit, {"state": {"auth_user_id": None}}, [])
    assert _status(sent) == 401
    assert active_at_start == [0]


@pytest.mark.asyncio
async def test_real_auth_middleware_invalid_token_gets_401_valid_token_passes() -> None:
    """With the real AuthMiddleware an invalid JWT is refused and a valid one reaches the route."""

    app = FastAPI()

    @app.post(UPLOAD_PATH)
    async def upload(file: UploadFile = File(...)) -> dict:
        return {"size": len(await file.read())}

    limit = UploadLimitMiddleware(app)
    stack = AuthMiddleware(limit)
    token = create_access_token(1, "uploader@example.com")

    async with _client(stack, {"Authorization": "Bearer not-a-jwt"}) as anonymous:
        refused = await anonymous.post(UPLOAD_PATH, files=_FILES)
    async with _client(stack, {"Authorization": f"Bearer {token}"}) as client:
        accepted = await client.post(UPLOAD_PATH, files=_FILES)

    assert refused.status_code == 401
    assert accepted.status_code == 200
    assert limit.active == 0


@pytest.mark.asyncio
async def test_refusal_order_is_401_then_503_then_413(monkeypatch: pytest.MonkeyPatch) -> None:
    """Authentication is checked before capacity, and capacity before the declared size."""

    monkeypatch.setattr(upload_limits, "MAX_UPLOAD_BODY_BYTES", 1024)
    oversize = {"file": ("a.bin", b"x" * 4096, "application/octet-stream")}
    gate, entered = asyncio.Event(), asyncio.Event()
    app, _ = _stack(gate, entered)

    async with _client(app) as client, _client(app, {}) as anonymous:
        holder = asyncio.create_task(client.post(UPLOAD_PATH, files=_FILES))
        await asyncio.wait_for(entered.wait(), timeout=5)

        anonymous_oversize = await asyncio.wait_for(anonymous.post(UPLOAD_PATH, files=oversize), 5)
        busy_oversize = await asyncio.wait_for(client.post(UPLOAD_PATH, files=oversize), 5)

        gate.set()
        await asyncio.wait_for(holder, 5)
        free_oversize = await asyncio.wait_for(client.post(UPLOAD_PATH, files=oversize), 5)

    assert anonymous_oversize.status_code == 401
    assert busy_oversize.status_code == 503
    assert free_oversize.status_code == 413


@pytest.mark.asyncio
async def test_declared_oversize_body_is_refused_without_a_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Content-Length above the body cap gets 413 before the route runs or a slot is taken."""

    monkeypatch.setattr(upload_limits, "MAX_UPLOAD_BODY_BYTES", 1024)
    entered = asyncio.Event()
    app, limit = _stack(entered=entered)

    async with _client(app) as client:
        response = await client.post(UPLOAD_PATH, files={"file": ("a.bin", b"x" * 4096, "application/octet-stream")})

    assert response.status_code == 413
    assert not entered.is_set()

    sent, active_at_start = await _call(limit, {"headers": [(b"content-length", b"4096")]}, [])
    assert _status(sent) == 413
    assert active_at_start == [0]


def _multipart(size: int, boundary: str = "septumboundary") -> bytes:
    return (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.bin\"\r\n"
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + b"x" * size + f"\r\n--{boundary}--\r\n".encode()


@pytest.mark.asyncio
async def test_streamed_oversize_body_gets_413_and_releases_its_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """A chunked body with no Content-Length is cut off with 413 past the cap, and frees its slot."""

    monkeypatch.setattr(upload_limits, "MAX_UPLOAD_BODY_BYTES", 1024)
    body = _multipart(4096)

    async def chunks() -> AsyncIterator[bytes]:
        for start in range(0, len(body), 512):
            yield body[start:start + 512]

    app, limit = _stack()
    async with _client(app) as client:
        response = await client.post(
            UPLOAD_PATH,
            content=chunks(),
            headers={**_AUTHED, "Content-Type": "multipart/form-data; boundary=septumboundary"},
        )
        after = await client.post(UPLOAD_PATH, files=_FILES)

    assert response.status_code == 413
    assert after.status_code == 200
    assert limit.active == 0


@pytest.mark.asyncio
async def test_slow_body_gets_408_and_releases_its_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """A client that stops sending its body mid-way is refused with 408 and frees its slot."""

    monkeypatch.setattr(upload_limits, "UPLOAD_READ_IDLE_TIMEOUT_SECONDS", 0.05)
    body = _multipart(2048)

    async def stalled() -> AsyncIterator[bytes]:
        yield body[:512]
        await asyncio.sleep(1)
        yield body[512:]

    app, limit = _stack()
    async with _client(app) as client:
        response = await client.post(
            UPLOAD_PATH,
            content=stalled(),
            headers={**_AUTHED, "Content-Type": "multipart/form-data; boundary=septumboundary"},
        )
        after = await client.post(UPLOAD_PATH, files=_FILES)

    assert response.status_code == 408
    assert after.status_code == 200
    assert limit.active == 0


@pytest.mark.asyncio
async def test_slow_drip_body_hits_the_overall_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """A body that keeps trickling in, never idle long enough for the idle timeout, still gets 408."""

    monkeypatch.setattr(upload_limits, "UPLOAD_READ_IDLE_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(upload_limits, "UPLOAD_BODY_TIMEOUT_SECONDS", 0.2)
    body = _multipart(4096)

    async def drip() -> AsyncIterator[bytes]:
        for start in range(0, len(body), 64):
            await asyncio.sleep(0.02)
            yield body[start:start + 64]

    app, limit = _stack()
    async with _client(app) as client:
        response = await client.post(
            UPLOAD_PATH,
            content=drip(),
            headers={**_AUTHED, "Content-Type": "multipart/form-data; boundary=septumboundary"},
        )
        after = await client.post(UPLOAD_PATH, files=_FILES)

    assert response.status_code == 408
    assert after.status_code == 200
    assert limit.active == 0


def test_content_type_parser_is_the_one_starlette_uses() -> None:
    """The middleware classifies with the same header parser Starlette's form parsing uses."""

    import python_multipart.multipart
    import starlette.formparsers
    import starlette.requests

    assert upload_limits.parse_options_header is python_multipart.multipart.parse_options_header
    assert starlette.requests.parse_options_header is python_multipart.multipart.parse_options_header
    assert starlette.formparsers.parse_options_header is python_multipart.multipart.parse_options_header


def test_body_timeout_default() -> None:
    assert upload_limits.UPLOAD_BODY_TIMEOUT_SECONDS == 900


@pytest.mark.parametrize(
    "content_type, bounded",
    [
        (b"multipart/form-data; boundary=x", True),
        (b"Multipart/Form-Data ; boundary=x", True),
        (b'multipart/form-data;boundary="x y"', True),
        (b"multipart/form-dataX; boundary=x", False),
        (b"application/json", False),
        (b"text/plain; note=multipart/form-data", False),
    ],
)
def test_content_type_is_classified_by_its_media_type(content_type: bytes, bounded: bool) -> None:
    scope = {"type": "http", "path": "/api/other", "root_path": "", "headers": [(b"content-type", content_type)]}

    assert upload_limits._is_bounded(scope) is bounded


@pytest.mark.asyncio
async def test_slot_is_released_when_the_response_starts() -> None:
    """The slot frees at ``http.response.start``, before the route's ``finally`` and body run."""

    _, limit = _stack()
    body = _multipart(16, boundary="b")
    active_seen: List[Tuple[str, int]] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict) -> None:
        active_seen.append((message["type"], limit.active))

    scope = {
        "type": "http",
        "method": "POST",
        "path": UPLOAD_PATH,
        "raw_path": UPLOAD_PATH.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"content-type", b"multipart/form-data; boundary=b")],
        "state": {"auth_user_id": 1},
    }
    await asyncio.wait_for(limit(scope, receive, send), 5)

    assert active_seen[0] == ("http.response.start", 0)
    assert all(active == 0 for _, active in active_seen)


@pytest.mark.asyncio
async def test_route_exception_releases_its_slot() -> None:
    """A route that raises still frees the slot."""

    app, limit = _stack(fail=True)
    async with _client(app, raise_app_exceptions=False) as client:
        response = await client.post(UPLOAD_PATH, files=_FILES)

    assert response.status_code == 500
    assert limit.active == 0


@pytest.mark.asyncio
async def test_client_disconnect_releases_its_slot() -> None:
    """A client that disconnects mid-body frees the slot."""

    _, limit = _stack()
    body = _multipart(2048, boundary="b")
    sent, _ = await _call(
        limit,
        {},
        [
            {"type": "http.request", "body": body[:512], "more_body": True},
            {"type": "http.disconnect"},
        ],
    )

    assert limit.active == 0
    assert _status(sent) is not None
    assert _status(sent) >= 400


@pytest.mark.asyncio
async def test_root_path_does_not_bypass_the_limit() -> None:
    """Behind a root_path the upload route is still bounded, even for a non-multipart body."""

    app, _ = _stack()
    async with _client(app, {}, root_path="/septum") as anonymous:
        response = await anonymous.post(
            "/septum" + UPLOAD_PATH,
            content=b"x",
            headers={"Content-Type": "application/octet-stream"},
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_cancelled_request_before_any_response_releases_its_slot() -> None:
    """If the app is cancelled before it answers, the slot is still given back."""

    entered = asyncio.Event()

    async def cancelled_app(scope: Scope, receive: Receive, send: Send) -> None:
        entered.set()
        raise asyncio.CancelledError

    limit = UploadLimitMiddleware(cancelled_app)

    with pytest.raises(asyncio.CancelledError):
        await _call(limit, {}, [])

    assert entered.is_set()
    assert limit.active == 0


@pytest.mark.asyncio
async def test_any_multipart_request_is_bounded_whatever_the_path() -> None:
    """The in-memory spool is process-wide, so every multipart request needs a caller; JSON does not."""

    app, _ = _stack()
    async with _client(app, {}) as anonymous:
        form = await anonymous.post("/api/other-form", files=_FILES)
        json_request = await anonymous.post("/api/other", json={})

    assert form.status_code == 401
    assert json_request.status_code == 200
