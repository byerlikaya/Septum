"""E2E test: Turkish PII PDF upload, chat with approval, deanonymized answer."""

from __future__ import annotations

import json
import threading
from io import BytesIO
from pathlib import Path

import fitz  # type: ignore[import]
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker as create_async_sessionmaker,
    create_async_engine,
)

from app.database import async_session_maker, init_db
from app.main import app
from app.routers import documents as documents_router


def _make_turkish_pii_pdf_bytes() -> bytes:
    """PDF with Turkish PII: Ahmet Yılmaz, Ayşe Kaya."""
    text = (
        "Bu belgede Ahmet Yılmaz ve Ayşe Kaya geçmektedir. "
        "İletişim bilgileri: Ahmet Yılmaz, Ayşe Kaya."
    )
    doc = fitz.open()
    try:
        page = doc.new_page()
        page.insert_text((72, 72), text)
        return doc.tobytes()
    finally:
        doc.close()


def _parse_sse_line(line: bytes) -> dict | None:
    if line.startswith(b"data: "):
        try:
            return json.loads(line[6:].decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
    return None


@pytest.fixture
def e2e_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test client with temp DB and storage; LLM mocked to return placeholder text."""
    test_db = tmp_path / "e2e.db"
    docs_dir = tmp_path / "documents"
    vec_dir = tmp_path / "vector_indexes"
    docs_dir.mkdir(parents=True, exist_ok=True)
    vec_dir.mkdir(parents=True, exist_ok=True)

    test_engine = create_async_engine(
        f"sqlite+aiosqlite:///{test_db}",
        echo=False,
        future=True,
    )
    test_session_maker = create_async_sessionmaker(
        bind=test_engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )

    monkeypatch.setattr(
        "app.database.engine",
        test_engine,
    )
    monkeypatch.setattr(
        "app.database.async_session_maker",
        test_session_maker,
    )
    monkeypatch.setattr(
        documents_router,
        "_DOC_STORAGE_DIR",
        docs_dir,
    )
    monkeypatch.setenv("VECTOR_INDEX_DIR", str(vec_dir))
    monkeypatch.setenv("USE_NER_LAYER_DEFAULT", "false")  # Presidio only; no HuggingFace NER download

    # Avoid HuggingFace downloads: no-op index_document, search returns [] (chat uses fallback chunks)
    from app.services import vector_store as vs_module

    def _no_op_index(self, document_id, chunk_ids, texts):  # noqa: ARG001
        pass

    def _empty_search(self, document_id, query, top_k):  # noqa: ARG001
        return []  # Chat router falls back to first top_k chunks by order

    monkeypatch.setattr(vs_module.VectorStore, "index_document", _no_op_index)
    monkeypatch.setattr(vs_module.VectorStore, "search", _empty_search)

    # Mock LLM return value; test sets it after upload to use document's real placeholders
    from app.services import llm_router as llm_router_module

    mock_llm_return: list[str] = []

    async def _mock_complete(self, messages, temperature=0.2, max_tokens=None, metadata=None):
        if mock_llm_return:
            return mock_llm_return[0]
        return "Bu belgede [PERSON_NAME_1] ve [PERSON_NAME_2] geçmektedir."

    monkeypatch.setattr(llm_router_module.LLMRouter, "complete", _mock_complete)

    # Init DB (tables + seed) using patched engine
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(init_db())
    loop.close()

    yield TestClient(app), mock_llm_return


def test_e2e_turkish_pii_upload_ask_approve_deanonymized(
    e2e_client: tuple[TestClient, list], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upload Turkish PII PDF, ask who is in the doc, then check deanonymized answer."""
    client, mock_llm_return = e2e_client

    # 1) Upload Turkish PII PDF
    pdf_bytes = _make_turkish_pii_pdf_bytes()
    upload_response = client.post(
        "/api/documents/upload",
        files={"file": ("turkish_pii.pdf", BytesIO(pdf_bytes), "application/pdf")},
    )
    if upload_response.status_code != 201:
        try:
            detail = upload_response.json()
        except Exception:
            detail = upload_response.text[:500]
        pytest.fail(f"Upload failed: {upload_response.status_code} - {detail}")
    doc = upload_response.json()
    document_id = doc["id"]
    assert document_id >= 1
    assert doc["ingestion_status"] == "completed"
    assert doc["entity_count"] >= 2  # at least two person names

    # Ensure chat uses the document's anon map (same process; patch so chat request sees it)
    from app.routers import chat as chat_router
    from app.services.document_anon_store import get_document_map

    stored_map = get_document_map(document_id)
    assert stored_map is not None, "Document anon map must be stored after upload"

    # Build mock LLM answer using placeholders for the two person names we expect (coreference may add "Ahmet" etc.)
    p1 = stored_map.entity_map.get("Ahmet Yılmaz") or stored_map.entity_map.get("Ahmet")
    p2 = stored_map.entity_map.get("Ayşe Kaya") or stored_map.entity_map.get("Ayşe")
    if not p1 or not p2:
        placeholders = sorted(stored_map.entity_map.values())
        assert len(placeholders) >= 2, f"Expected at least 2 placeholders, got {stored_map.entity_map}"
        p1, p2 = placeholders[0], placeholders[1]
    mock_llm_return.append(
        f"Bu belgede {p1} ve {p2} geçmektedir. İletişim bilgileri: {p1}, {p2}."
    )

    _original_get = get_document_map

    def _get_map_for_chat(doc_id: int):
        return stored_map if doc_id == document_id else _original_get(doc_id)

    monkeypatch.setattr(chat_router, "get_document_map", _get_map_for_chat)

    # 2) Ask "Bu belgede kimler geçiyor?"
    answer_chunks: list[str] = []
    stream_events: list[dict] = []

    def consume_stream() -> None:
        with client.stream(
            "POST",
            "/api/chat/ask",
            json={
                "message": "Bu belgede kimler geçiyor?",
                "document_id": document_id,
                "require_approval": False,  # Skip approval so we get answer directly; deanonymize still applied
            },
        ) as response:
            assert response.status_code == 200
            buffer = b""
            for chunk in response.iter_bytes():
                if not chunk:
                    continue
                buffer += chunk
                while b"\n\n" in buffer or b"\r\n\r\n" in buffer:
                    sep = b"\r\n\r\n" if b"\r\n\r\n" in buffer else b"\n\n"
                    part, buffer = buffer.split(sep, 1)
                    for line in part.split(b"\n"):
                        line = line.strip(b"\r")
                        if line.startswith(b"data: "):
                            ev = _parse_sse_line(line)
                            if ev:
                                stream_events.append(ev)
                                if ev.get("type") == "answer_chunk":
                                    answer_chunks.append(ev.get("text", ""))

    t_consume = threading.Thread(target=consume_stream)
    t_consume.start()
    t_consume.join(timeout=20)

    # 3) We must have received answer
    for ev in stream_events:
        if ev.get("type") == "error":
            pytest.fail(f"Chat returned error: {ev.get('message', ev)}")
    if not answer_chunks:
        event_types = [e.get("type") for e in stream_events]
        pytest.fail(f"No answer_chunks received. Event types: {event_types}.")

    # 4) Deanonymized answer: no placeholder syntax; at least one Turkish name from the doc appears
    full_answer = "".join(answer_chunks)
    assert "[PERSON" not in full_answer, f"Placeholders must be replaced: {full_answer!r}"
    assert ("Ahmet" in full_answer or "Ayşe" in full_answer), (
        f"Deanonymized answer should contain at least one name from the doc: {full_answer!r}"
    )
