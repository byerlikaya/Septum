from __future__ import annotations

"""Detection settings are part of the policy digest.

Turning a detection layer on or off, or pointing a language at another NER
model, changes which spans the document sanitizer finds, so documents
sanitized before the change must be reported stale.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from septum_api.database import build_default_app_settings, get_db
from septum_api.main import app
from septum_api.models import Base
from septum_api.models.document import Document
from septum_api.models.settings import AppSettings
from septum_api.models.user import User
from septum_api.services import document_pipeline as dp
from septum_api.services.policy_snapshot import current_policy_snapshot
from septum_api.utils.auth_dependency import get_current_user
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[dict[str, Any]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'settings.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _override_get_db():
        async with sm() as session:
            yield session

    async with sm() as session:
        user = User(email="owner@example.com", hashed_password="x", role="editor")
        session.add_all([user, build_default_app_settings()])
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user] = lambda: user
        try:
            yield {"sm": sm, "client": client, "user": user}
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)
            await engine.dispose()


async def _fresh_document(env: dict[str, Any]) -> int:
    async with env["sm"]() as session:
        snapshot = await current_policy_snapshot(session)
        doc = Document(
            filename="f", original_filename="f.pdf", file_type="application/pdf",
            file_format="pdf", detected_language="en", encrypted_path="/x",
            file_size_bytes=1, active_regulation_ids=[], ingestion_status="completed",
            sanitized_entity_types=snapshot.entity_types,
            sanitized_policy_digest=snapshot.digest, user_id=env["user"].id,
        )
        session.add(doc)
        await session.commit()
        return doc.id


async def _needs_reprocessing(env: dict[str, Any], doc_id: int) -> bool:
    resp = await env["client"].get(f"/api/documents/{doc_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()["needs_reprocessing"]


async def _update_settings(env: dict[str, Any], **values: Any) -> None:
    async with env["sm"]() as session:
        row = await session.get(AppSettings, 1)
        for name, value in values.items():
            setattr(row, name, value)
        await session.commit()


@pytest.mark.parametrize(
    "field",
    [
        "use_ner_layer",
        "use_presidio_layer",
        "use_ollama_validation_layer",
        "use_ollama_semantic_layer",
    ],
)
async def test_toggling_a_detection_layer_flags_documents(
    env: dict[str, Any], field: str
) -> None:
    doc_id = await _fresh_document(env)
    assert await _needs_reprocessing(env, doc_id) is False
    async with env["sm"]() as session:
        current = getattr(await session.get(AppSettings, 1), field)

    await _update_settings(env, **{field: not current})

    assert await _needs_reprocessing(env, doc_id) is True


async def test_changed_ner_model_override_flags_documents(env: dict[str, Any]) -> None:
    await _update_settings(env, ner_model_overrides={"en": "acme/ner"})
    doc_id = await _fresh_document(env)
    assert await _needs_reprocessing(env, doc_id) is False

    await _update_settings(env, ner_model_overrides={"en": "acme/other-ner"})

    assert await _needs_reprocessing(env, doc_id) is True


async def test_ollama_url_change_does_not_flag_documents(env: dict[str, Any]) -> None:
    await _update_settings(env, use_ollama_validation_layer=True)
    doc_id = await _fresh_document(env)

    await _update_settings(env, ollama_base_url="http://localhost:11999")

    assert await _needs_reprocessing(env, doc_id) is False


@pytest.mark.parametrize(
    "layer", ["use_ollama_validation_layer", "use_ollama_semantic_layer"]
)
async def test_validation_model_change_flags_documents_while_a_layer_uses_it(
    env: dict[str, Any], layer: str
) -> None:
    await _update_settings(
        env, use_ollama_validation_layer=False, use_ollama_semantic_layer=False
    )
    await _update_settings(env, **{layer: True})
    doc_id = await _fresh_document(env)

    await _update_settings(env, ollama_deanon_model="other:1b")

    assert await _needs_reprocessing(env, doc_id) is True


async def test_validation_model_change_is_ignored_while_no_layer_uses_it(
    env: dict[str, Any],
) -> None:
    await _update_settings(
        env, use_ollama_validation_layer=False, use_ollama_semantic_layer=False
    )
    doc_id = await _fresh_document(env)

    await _update_settings(env, ollama_deanon_model="other:1b")

    assert await _needs_reprocessing(env, doc_id) is False


async def test_pipeline_digest_uses_the_settings_it_sanitized_with(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Lock:
        async def ensure_held(self) -> None:
            return None

    @asynccontextmanager
    async def _map_lock(document_id):
        yield _Lock()

    async def _noop(*args, **kwargs):
        return None

    async def _no_chunks(self, file_format, text):
        return []

    async def _persist(self, db, document_id, chunks, raw_texts):
        return []

    for name, value in (
        ("create_sanitizer", _noop),
        ("document_map_lock", _map_lock),
        ("set_document_map", _noop),
        ("replace_index_for_document", _noop),
        ("recompute_relationships_for_document", _noop),
    ):
        monkeypatch.setattr(dp, name, value)
    monkeypatch.setattr(dp.DocumentPipeline, "_build_chunks", _no_chunks)
    monkeypatch.setattr(dp.DocumentPipeline, "_persist_chunks", _persist)
    await _update_settings(env, use_ner_layer=False, ner_model_overrides={"en": "acme/ner"})

    async with env["sm"]() as session:
        settings = await session.get(AppSettings, 1)
        doc = Document(
            filename="f", original_filename="f.pdf", file_type="application/pdf",
            file_format="pdf", detected_language="en", encrypted_path="/x",
            file_size_bytes=1, active_regulation_ids=[], ingestion_status="processing",
            user_id=env["user"].id,
        )
        session.add(doc)
        await session.commit()
        await dp.DocumentPipeline(settings=settings).run(
            db=session, document=doc, file_format="pdf",
            ingested_text="", ingestion_confidence=None,
        )
        expected = (await current_policy_snapshot(session)).digest

    async with env["sm"]() as session:
        stored = await session.get(Document, doc.id)
    assert stored.sanitized_policy_digest == expected
    assert await _needs_reprocessing(env, doc.id) is False
    await _update_settings(env, use_ner_layer=True)
    assert await _needs_reprocessing(env, doc.id) is True
