"""Shared ``env`` fixture: an isolated document store with an authenticated client.

Registered as a pytest plugin from ``conftest.py``, so any test module can take
``env`` as a parameter without importing it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from septum_api import database as database_module
from septum_api.database import _sqlite_wal_connect, get_db
from septum_api.main import app
from septum_api.models import Base
from septum_api.models.document import Chunk, Document
from septum_api.models.entity_detection import EntityDetection
from septum_api.models.user import User
from septum_api.services import document_anon_store
from septum_api.services import document_pipeline as pipeline_module
from septum_api.utils.auth_dependency import get_current_user


class _NoopIndex:
    def delete_index(self, document_id: int) -> None:
        return None


class _Env:
    def __init__(
        self, session_maker: Any, client: AsyncClient, user: User, tmp: Path
    ) -> None:
        self.session_maker = session_maker
        self.client = client
        self.user = user
        self.tmp = tmp

    async def add_document(
        self, status: str = "completed", detections: int = 1
    ) -> tuple[int, int]:
        source = self.tmp / f"source-{status}-{id(object())}"
        source.write_bytes(b"ciphertext")
        async with self.session_maker() as session:
            doc = Document(
                filename=source.name,
                original_filename="report.pdf",
                file_type="application/pdf",
                file_format="pdf",
                detected_language="en",
                encrypted_path=str(source),
                file_size_bytes=10,
                ocr_confidence=0.0,
                active_regulation_ids=[],
                ingestion_status=status,
                chunk_count=1,
                user_id=self.user.id,
            )
            session.add(doc)
            await session.flush()
            chunk = Chunk(
                document_id=doc.id,
                index=0,
                sanitized_text="[PERSON_1] signed.",
                char_count=18,
            )
            session.add(chunk)
            await session.flush()
            for _ in range(detections):
                session.add(
                    EntityDetection(
                        document_id=doc.id,
                        chunk_id=chunk.id,
                        entity_type="PERSON",
                        placeholder="[PERSON_1]",
                        start_offset=0,
                        end_offset=5,
                        score=0.9,
                    )
                )
            await session.commit()
            return doc.id, chunk.id

    async def count(self, model: Any, document_id: int) -> int:
        async with self.session_maker() as session:
            return (
                await session.execute(
                    select(func.count())
                    .select_from(model)
                    .where(model.document_id == document_id)
                )
            ).scalar_one()

    async def document(self, document_id: int) -> Document | None:
        async with self.session_maker() as session:
            return await session.get(Document, document_id)


@pytest.fixture
async def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Env]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'erasure.db'}")
    event.listen(engine.sync_engine, "connect", _sqlite_wal_connect)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _override_get_db():
        async with session_maker() as session:
            yield session

    async with session_maker() as session:
        user = User(email="owner@example.com", hashed_password="x", role="editor")
        session.add(user)
        await session.commit()

    monkeypatch.setattr(document_anon_store, "_ANON_MAP_DIR", tmp_path / "maps")
    monkeypatch.setattr(document_anon_store, "_document_maps", {})
    monkeypatch.setattr(document_anon_store, "_map_versions", {})
    monkeypatch.setattr(pipeline_module, "VectorStore", _NoopIndex)
    monkeypatch.setattr(pipeline_module, "BM25Retriever", _NoopIndex)
    monkeypatch.setattr(database_module, "get_session_maker", lambda: session_maker)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user] = lambda: user
        try:
            yield _Env(session_maker, client, user, tmp_path)
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)
            await engine.dispose()
