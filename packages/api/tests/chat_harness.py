"""Shared chat-router test harness: an isolated database plus mocked LLM, retrieval and sanitizer.

The ``chat_env`` fixture in ``conftest.py`` wraps :func:`open_chat_env`.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import pytest
from httpx import ASGITransport, AsyncClient
from septum_core.spans import SanitizeResult
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from septum_api.database import build_default_app_settings, get_db
from septum_api.main import app
from septum_api.models import Base
from septum_api.models.document import Chunk, Document
from septum_api.models.user import User
from septum_api.routers import chat as chat_module
from septum_api.services import pii_gate
from septum_api.services.approval_gate import ApprovalDecision
from septum_api.services.llm_router import LLMRouter
from septum_api.services.policy_snapshot import current_policy_snapshot
from septum_api.utils.auth_dependency import get_current_user

RAW_VALUE = "Jane Roe"
PLACEHOLDER = "[PERSON_NAME_1]"


class ChatEnv:
    """Handle to the per-test database, acting user and captured LLM prompts."""

    def __init__(self, session_maker: Any, client: AsyncClient) -> None:
        self.session_maker = session_maker
        self.client = client
        self.current_user: Optional[User] = None
        self.prompts: list[str] = []
        self.users: dict[str, User] = {}
        self.gate = _FakeGate()

    def act_as(self, name: str) -> None:
        self.current_user = self.users[name]

    async def add_document(
        self, owner: Optional[User], filename: str, chunk_texts: list[str]
    ) -> int:
        async with self.session_maker() as session:
            doc = Document(
                filename=f"stored-{filename}",
                original_filename=filename,
                file_type="application/pdf",
                file_format="pdf",
                detected_language="en",
                encrypted_path="/nonexistent",
                file_size_bytes=10,
                ocr_confidence=0.0,
                active_regulation_ids=[],
                sanitized_entity_types=[],
                sanitized_policy_digest=(await current_policy_snapshot(session)).digest,
                sanitized_language="en",
                ingestion_status="completed",
                chunk_count=len(chunk_texts),
                user_id=owner.id if owner is not None else None,
            )
            session.add(doc)
            await session.flush()
            for idx, text in enumerate(chunk_texts):
                session.add(
                    Chunk(
                        document_id=doc.id,
                        index=idx,
                        sanitized_text=text,
                        char_count=len(text),
                    )
                )
            await session.commit()
            return doc.id

    async def set_require_approval(self, value: bool) -> None:
        async with self.session_maker() as session:
            settings = build_default_app_settings()
            settings.require_approval = value
            session.add(settings)
            await session.commit()

    async def ask(self, payload: dict[str, Any]) -> tuple[int, list[dict[str, Any]], Any]:
        resp = await self.client.post("/api/chat/ask", json=payload)
        if resp.status_code != 200:
            return resp.status_code, [], resp.json()
        events = [
            json.loads(line[len("data: "):])
            for line in resp.text.splitlines()
            if line.startswith("data: ")
        ]
        return resp.status_code, events, None


class _FakeGate:
    """Approval gate double that records the request and rejects it."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> None:
        self.created.append(kwargs)

    def discard_session(self, session_id: str, handle: Any) -> None:
        pass

    async def wait_for_approval(self, session_id: str, timeout: float) -> ApprovalDecision:
        return ApprovalDecision(approved=False, chunks=[], reason="test")


class _NoEntitySanitizer:
    """Sanitizer double that detects nothing, so no NER model is loaded."""

    def sanitize(self, text: str, language: str, anon_map: Any) -> SanitizeResult:
        return SanitizeResult(sanitized_text=text, entity_count=0, detected_spans=[])


async def _create_sanitizer(
    db: Any, settings: Any, enable_ollama: bool = False, policy: Any = None
) -> _NoEntitySanitizer:
    return _NoEntitySanitizer()


class _FakeDeanonymizer:
    """Deterministic local de-anonymizer: restores the one known placeholder."""

    def __init__(self, settings: Any) -> None:
        self._settings = settings

    async def deanonymize(self, text: str, anon_map: Any, **kwargs: Any) -> str:
        return text.replace(PLACEHOLDER, RAW_VALUE)


@asynccontextmanager
async def open_chat_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[ChatEnv]:
    """Isolated chat database and app overrides with every LLM, retrieval and sanitizer call mocked."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _override_get_db():
        async with session_maker() as session:
            yield session

    async with session_maker() as session:
        users = {
            "admin": User(email="admin@example.com", hashed_password="x", role="admin"),
            "alice": User(email="alice@example.com", hashed_password="x", role="editor"),
            "bob": User(email="bob@example.com", hashed_password="x", role="editor"),
        }
        session.add_all(users.values())
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        env = ChatEnv(session_maker, client)
        env.users = users

        async def _sanitize(message, language, settings, anon_map, db):
            return message, 0, {}

        async def _retrieve(db, document_id, query, top_k, chunk_count, relevance_threshold):
            rows = await db.execute(select(Chunk).where(Chunk.document_id == document_id))
            return list(rows.scalars().all())

        async def _retrieve_all(db, query, top_k, documents, relevance_threshold):
            ids = [d.id for d in documents]
            rows = await db.execute(select(Chunk).where(Chunk.document_id.in_(ids)))
            chunks = list(rows.scalars().all())
            return chunks, sorted({c.document_id for c in chunks})

        async def _no_map(document_id):
            return None

        async def _classify(**kwargs):
            return "search"

        async def _complete(self, messages, **kwargs):
            env.prompts.append(messages[-1]["content"])
            return f"The person is {PLACEHOLDER}."

        monkeypatch.setattr(chat_module, "_sanitize_query", _sanitize)
        monkeypatch.setattr(chat_module, "_retrieve_chunks", _retrieve)
        monkeypatch.setattr(chat_module, "_retrieve_chunks_all_documents", _retrieve_all)
        monkeypatch.setattr(chat_module, "get_document_map", _no_map)
        monkeypatch.setattr(chat_module, "_classify_query_intent", _classify)
        monkeypatch.setattr(chat_module, "Deanonymizer", _FakeDeanonymizer)
        monkeypatch.setattr(chat_module, "create_sanitizer", _create_sanitizer)
        monkeypatch.setattr(pii_gate, "create_sanitizer", _create_sanitizer)
        monkeypatch.setattr(chat_module, "get_approval_gate", lambda: env.gate)
        monkeypatch.setattr(LLMRouter, "complete", _complete)

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user] = lambda: env.current_user
        try:
            yield env
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)
            await engine.dispose()


def answer_text(events: list[dict[str, Any]]) -> str:
    """Concatenate the streamed answer chunks."""
    return "".join(e["text"] for e in events if e["type"] == "answer_chunk")
