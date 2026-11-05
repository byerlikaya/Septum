"""Every ``/api/`` response is kept out of browser and proxy caches; other paths are not forced."""

from __future__ import annotations

from pathlib import Path

from document_env_fixtures import _Env
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, StreamingResponse
from starlette.routing import Route

from septum_api.middleware.security_headers import SecurityHeadersMiddleware
from septum_api.models.document import Document
from septum_api.utils.crypto import encrypt


def _assert_no_store(resp) -> None:
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["pragma"] == "no-cache"


async def test_raw_document_is_not_cacheable(env: _Env) -> None:
    doc, _ = await env.add_document()
    async with env.session_maker() as session:
        document = await session.get(Document, doc)
        Path(document.encrypted_path).write_bytes(encrypt(b"%PDF-1.7 Jane Doe"))

    resp = await env.client.get(f"/api/documents/{doc}/raw")

    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.7 Jane Doe"
    assert resp.headers["content-disposition"] == 'inline; filename="report.pdf"'
    _assert_no_store(resp)


async def test_document_metadata_is_not_cacheable(env: _Env) -> None:
    doc, _ = await env.add_document()

    for path in ("/api/documents", f"/api/documents/{doc}"):
        resp = await env.client.get(path)
        assert resp.status_code == 200, path
        _assert_no_store(resp)


async def test_chunks_are_not_cacheable(env: _Env) -> None:
    doc, chunk = await env.add_document()

    for path, params in (
        ("/api/chunks", {"document_id": doc}),
        (f"/api/chunks/{chunk}", None),
    ):
        resp = await env.client.get(path, params=params)
        assert resp.status_code == 200, path
        _assert_no_store(resp)


async def test_chat_sessions_are_not_cacheable(env: _Env) -> None:
    created = await env.client.post("/api/chat-sessions", json={"title": "t"})
    assert created.status_code == 201
    session_id = created.json()["id"]

    for path in (
        "/api/chat-sessions",
        f"/api/chat-sessions/{session_id}",
        f"/api/chat-sessions/{session_id}/export",
    ):
        resp = await env.client.get(path)
        assert resp.status_code == 200, path
        _assert_no_store(resp)


async def test_non_api_path_is_not_forced(env: _Env) -> None:
    resp = await env.client.get("/openapi.json")

    assert resp.status_code == 200
    assert "cache-control" not in resp.headers
    assert "pragma" not in resp.headers


def _probe_client(root_path: str = "") -> AsyncClient:
    async def stream(request):
        async def body():
            yield b"data: one\n\n"
            yield b"data: two\n\n"

        return StreamingResponse(body(), media_type="text/event-stream")

    async def cached(request):
        return PlainTextResponse("x", headers={"Cache-Control": "private, max-age=60"})

    probe = Starlette(routes=[Route("/api/stream", stream), Route("/api/cached", cached)])
    probe.add_middleware(SecurityHeadersMiddleware)
    return AsyncClient(
        transport=ASGITransport(app=probe, root_path=root_path), base_url="http://t"
    )


async def test_streaming_response_streams_and_is_not_cacheable() -> None:
    async with _probe_client() as client:
        async with client.stream("GET", "/api/stream") as resp:
            chunks = [chunk async for chunk in resp.aiter_bytes()]

    assert b"".join(chunks) == b"data: one\n\ndata: two\n\n"
    _assert_no_store(resp)


async def test_explicit_cache_control_is_kept() -> None:
    async with _probe_client() as client:
        resp = await client.get("/api/cached")

    assert resp.headers["cache-control"] == "private, max-age=60"
    assert resp.headers["pragma"] == "no-cache"


async def test_api_path_under_root_path_is_not_cacheable() -> None:
    async with _probe_client(root_path="/septum") as client:
        resp = await client.get("/septum/api/stream")

    assert resp.status_code == 200
    _assert_no_store(resp)


async def test_root_path_is_stripped_only_at_a_segment_boundary() -> None:
    async with _probe_client(root_path="/ap") as client:
        resp = await client.get("/api/stream")

    assert resp.status_code == 200
    _assert_no_store(resp)
