from __future__ import annotations

"""Tests for chat prompt construction with/without document context."""

from backend.app.services.chat_context import ChatContextPayload, build_chat_prompt


def test_build_chat_prompt_without_context_avoids_document_refusal_rule() -> None:
    payload = ChatContextPayload(
        sanitized_query="Test query",
        context_chunks=[],
        regulation_names=["GDPR"],
        language="tr",
        schema_instruction="",
        placeholder_list_str="",
        output_mode="chat",
    )

    prompt = build_chat_prompt(payload)

    assert "did not provide any document context" in prompt
    assert "Do NOT claim that information is missing from a document." in prompt
    assert "I cannot find that information in the document." not in prompt


def test_build_chat_prompt_with_context_keeps_document_refusal_rule() -> None:
    payload = ChatContextPayload(
        sanitized_query="Test query",
        context_chunks=["Chunk body"],
        regulation_names=["GDPR"],
        language="en",
        schema_instruction="",
        placeholder_list_str="",
        output_mode="chat",
    )

    prompt = build_chat_prompt(payload)

    assert "Relevant context (sanitized):" in prompt
    assert "I cannot find that information in the document." in prompt
