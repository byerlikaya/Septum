from __future__ import annotations

"""``GET /api/documents/progress`` is owner-scoped and bounded like the list."""

from pathlib import Path
from typing import Any, AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from septum_api.database import get_db
from septum_api.main import app
from septum_api.models import Base
from septum_api.models.document import Document
from septum_api.models.user import User
from septum_api.request_limits import MAX_PROGRESS_IDS
from septum_api.services import document_progress
from septum_api.utils.auth_dependency import get_current_user
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[dict[str, Any]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'progress.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _override_get_db():
        async with sm() as session:
            yield session

    async with sm() as session:
        users = {
            role: User(email=f"{role}@example.com", hashed_password="x", role=role)
            for role in ("editor", "viewer", "admin")
        }
        session.add_all(users.values())
        await session.flush()
        docs = {}
        for owner in ("editor", "viewer"):
            doc = Document(
                filename="f", original_filename="f.pdf", file_type="application/pdf",
                file_format="pdf", detected_language="en", encrypted_path="/x",
                file_size_bytes=1, active_regulation_ids=[], ingestion_status="processing",
                user_id=users[owner].id,
            )
            session.add(doc)
            await session.flush()
            docs[owner] = doc.id
        await session.commit()

    state: dict[str, Any] = {"users": users, "docs": docs, "current": users["editor"]}
    for did in docs.values():
        document_progress.set_progress(did, 42)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        state["client"] = client
        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user] = lambda: state["current"]
        try:
            yield state
        finally:
            for did in docs.values():
                document_progress.clear_progress(did)
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)
            await engine.dispose()


async def _progress(env: dict[str, Any], ids: str) -> Any:
    return await env["client"].get("/api/documents/progress", params={"ids": ids})


async def test_other_users_documents_are_left_out(env: dict[str, Any]) -> None:
    mine, theirs = env["docs"]["editor"], env["docs"]["viewer"]

    resp = await _progress(env, f"{mine},{theirs},999999")

    assert resp.status_code == 200
    assert resp.json() == {str(mine): 42}


async def test_admin_sees_every_document(env: dict[str, Any]) -> None:
    env["current"] = env["users"]["admin"]
    ids = sorted(env["docs"].values())

    resp = await _progress(env, ",".join(map(str, ids)))

    assert resp.json() == {str(i): 42 for i in ids}


async def test_malformed_entries_are_ignored(env: dict[str, Any]) -> None:
    mine = env["docs"]["editor"]

    resp = await _progress(env, f" {mine} ,abc,²,-1,")

    assert resp.status_code == 200
    assert resp.json() == {str(mine): 42}


async def test_ids_beyond_the_32_bit_range_are_dropped(env: dict[str, Any]) -> None:
    mine = env["docs"]["editor"]

    resp = await _progress(env, f"{mine},{2**31},99999999999,{2**31 - 1}")

    assert resp.status_code == 200
    assert resp.json() == {str(mine): 42}


async def test_too_many_ids_is_422(env: dict[str, Any]) -> None:
    resp = await _progress(env, ",".join(str(i) for i in range(1, MAX_PROGRESS_IDS + 2)))

    assert resp.status_code == 422
    resp = await _progress(env, "1" * (MAX_PROGRESS_IDS * 20 + 1))
    assert resp.status_code == 422


async def test_empty_ids_return_nothing(env: dict[str, Any]) -> None:
    resp = await _progress(env, "")

    assert resp.status_code == 200
    assert resp.json() == {}
