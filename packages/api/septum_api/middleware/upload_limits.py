"""Memory bounds for document uploads, which stay in RAM from socket to encryption.

A raw upload is plaintext personal data until the upload endpoint encrypts
it, so multipart file parts are never spooled to a temporary file. That
setting is process-wide, so every multipart request (and the upload route
under any content type) is bounded here: only authenticated callers may send
one, the body is capped and must keep arriving, and only a fixed number may
be in flight per worker process; the rest are refused with 503 + ``Retry-After``.
"""

from __future__ import annotations

import asyncio

from fastapi import HTTPException
from prometheus_client import Counter
from python_multipart.multipart import parse_options_header
from starlette._utils import get_route_path
from starlette.formparsers import MultiPartParser
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..env_flags import env_int

MAX_UPLOAD_BYTES = env_int("MAX_UPLOAD_BYTES", 500 * 1024 * 1024, 1, 2**40)
"""Largest file an upload may carry; the endpoint refuses more with 413."""

MULTIPART_OVERHEAD_BYTES = 64 * 1024
"""Room for multipart boundaries and part headers on top of the file itself."""

MAX_UPLOAD_BODY_BYTES = MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD_BYTES
"""Largest request body the upload route reads before refusing with 413."""

MAX_CONCURRENT_UPLOADS = env_int("MAX_CONCURRENT_UPLOADS", 1, 1, 64)
"""Uploads one worker process accepts at once.

Each upload peaks at about twice its file size in RAM (the parsed body plus
the buffer it is encrypted in), so every extra slot adds up to about
2 x ``MAX_UPLOAD_BYTES`` to a worker's peak memory.
"""

UPLOAD_READ_IDLE_TIMEOUT_SECONDS = 30.0
"""Longest gap between body chunks before a bounded request is refused with 408."""

UPLOAD_BODY_TIMEOUT_SECONDS = env_int("UPLOAD_BODY_TIMEOUT_SECONDS", 900, 10, 86400)
"""Longest a bounded request may take to send its whole body before 408.

Stops a slow-drip client that never pauses long enough for the idle timeout
from holding an upload slot; the default allows a full 500 MB upload at
about 0.6 MB/s.
"""

UPLOAD_BUSY_RETRY_AFTER_SECONDS = 5

UPLOAD_PATH = "/api/documents/upload"

_MULTIPART_CONTENT_TYPE = b"multipart/form-data"

UPLOADS_REFUSED = Counter(
    "septum_uploads_refused_total",
    "Document uploads refused because the per-worker upload limit was reached",
)


def keep_multipart_uploads_in_memory() -> None:
    """Make multipart file parts up to the body cap stay in RAM instead of a temp file."""
    MultiPartParser.spool_max_size = MAX_UPLOAD_BODY_BYTES


TOO_LARGE_DETAIL = (
    f"Upload exceeds {MAX_UPLOAD_BYTES} bytes. Set MAX_UPLOAD_BYTES to raise the cap."
)


def _payload_too_large() -> HTTPException:
    """413 raised while the route reads the body, so the app's error handler answers it."""
    return HTTPException(status_code=413, detail=TOO_LARGE_DETAIL)


def _read_timed_out() -> HTTPException:
    """408 raised when the client stops sending the body."""
    return HTTPException(status_code=408, detail="Request body was not received in time.")


def _is_bounded(scope: Scope) -> bool:
    """Whether a request may make the server hold an upload in memory."""
    if scope["type"] != "http":
        return False
    if get_route_path(scope) == UPLOAD_PATH:
        return True
    for name, value in scope.get("headers", ()):
        if name == b"content-type":
            content_type, _ = parse_options_header(value)
            return content_type.lower() == _MULTIPART_CONTENT_TYPE
    return False


class UploadLimitMiddleware:
    """Bound every request that could make the server hold an upload in memory.

    Must sit inside ``AuthMiddleware``, which records the caller in the
    request state. Checks run in this order: 401 for a caller without a
    valid credential (role checks only run after the body is parsed, so an
    anonymous client could otherwise hold a slot), 503 when every slot is
    taken, 413 for a declared body over the cap. Only then is a slot taken.
    While the body is read it is cut off with 413 past the cap, and with 408
    after :data:`UPLOAD_READ_IDLE_TIMEOUT_SECONDS` without data or once
    :data:`UPLOAD_BODY_TIMEOUT_SECONDS` have passed since the slot was taken. The slot is
    released once the endpoint answers (the response start), when its
    in-memory copies are gone; the ingestion it schedules is bounded
    separately.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.active = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not _is_bounded(scope):
            await self.app(scope, receive, send)
            return

        if scope.get("state", {}).get("auth_user_id") is None:
            await JSONResponse(
                status_code=401,
                content={"detail": "Not authenticated"},
                headers={"WWW-Authenticate": "Bearer"},
            )(scope, receive, send)
            return

        if self.active >= MAX_CONCURRENT_UPLOADS:
            UPLOADS_REFUSED.inc()
            response = JSONResponse(
                status_code=503,
                content={"detail": "Server is busy, try again shortly.", "code": "server_busy"},
                headers={"Retry-After": str(UPLOAD_BUSY_RETRY_AFTER_SECONDS)},
            )
            await response(scope, receive, send)
            return

        for name, value in scope.get("headers", ()):
            if name == b"content-length" and value.isdigit() and int(value) > MAX_UPLOAD_BODY_BYTES:
                await JSONResponse(status_code=413, content={"detail": TOO_LARGE_DETAIL})(
                    scope, receive, send
                )
                return

        self.active += 1
        released = False
        received = 0
        body_complete = False
        loop = asyncio.get_running_loop()
        body_deadline = loop.time() + UPLOAD_BODY_TIMEOUT_SECONDS

        def release() -> None:
            nonlocal released
            if not released:
                released = True
                self.active -= 1

        async def limited_receive() -> Message:
            nonlocal received, body_complete
            if body_complete:
                return await receive()
            remaining = body_deadline - loop.time()
            if remaining <= 0:
                raise _read_timed_out()
            try:
                message = await asyncio.wait_for(
                    receive(), min(UPLOAD_READ_IDLE_TIMEOUT_SECONDS, remaining)
                )
            except TimeoutError:
                raise _read_timed_out() from None
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > MAX_UPLOAD_BODY_BYTES:
                    raise _payload_too_large()
                body_complete = not message.get("more_body", False)
            return message

        async def releasing_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                release()
            await send(message)

        try:
            await self.app(scope, limited_receive, releasing_send)
        finally:
            release()
