"""The chunk API returns each chunk's character range in the document's joined text."""

from __future__ import annotations

from document_env_fixtures import _Env
from septum_api.models.document import Chunk


async def test_chunk_responses_carry_the_stored_character_range(env: _Env) -> None:
    doc, chunk_id = await env.add_document()
    async with env.session_maker() as session:
        chunk = await session.get(Chunk, chunk_id)
        chunk.start_char = 40
        chunk.end_char = 58
        await session.commit()

    single = await env.client.get(f"/api/chunks/{chunk_id}")
    listed = await env.client.get("/api/chunks", params={"document_id": doc})

    assert single.status_code == 200 and listed.status_code == 200
    assert (single.json()["start_char"], single.json()["end_char"]) == (40, 58)
    item = listed.json()["items"][0]
    assert (item["start_char"], item["end_char"]) == (40, 58)


async def test_chunks_without_a_stored_range_return_null(env: _Env) -> None:
    _, chunk_id = await env.add_document()

    body = (await env.client.get(f"/api/chunks/{chunk_id}")).json()

    assert body["start_char"] is None and body["end_char"] is None
