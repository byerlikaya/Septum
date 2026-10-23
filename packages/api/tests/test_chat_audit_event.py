"""The chat path's own ``pii_detected`` audit row holds no question text.

Drives the real ``/api/chat/ask`` audit write (LLM, retrieval and sanitizer
mocked by ``chat_env``) and reads the stored row back. It catches a write
that bypasses the model's ``@validates`` guard (a Core insert or update, an
in-place mutation flushed with ``flag_modified``); constructor and dict
writes are guarded by the AST writer scan.
"""

from __future__ import annotations

import pytest
from chat_harness import PLACEHOLDER, ChatEnv
from sqlalchemy import select

from septum_api.models.audit_event import AuditEvent
from septum_api.routers import chat as chat_module


async def test_chat_pii_audit_event_stores_no_masked_query(
    chat_env: ChatEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    masked = f"Who is {PLACEHOLDER}?"

    async def _sanitize(message, language, settings, anon_map, db):
        return masked, 1, {"PERSON_NAME": 1}

    monkeypatch.setattr(chat_module, "_sanitize_query", _sanitize)
    await chat_env.set_require_approval(False)
    doc_id = await chat_env.add_document(chat_env.users["alice"], "a.pdf", ["alpha"])
    chat_env.act_as("alice")

    status, _events, _ = await chat_env.ask({"message": "Who is Jane Roe?", "document_ids": [doc_id]})

    assert status == 200
    async with chat_env.session_maker() as session:
        rows = await session.execute(select(AuditEvent).where(AuditEvent.event_type == "pii_detected"))
        events = rows.scalars().all()
    assert [(e.entity_types_detected, e.extra) for e in events] == [
        ({"PERSON_NAME": 1}, {"source": "chat_query"})
    ]
