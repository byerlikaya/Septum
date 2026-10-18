from __future__ import annotations

"""Tests for chat prompt construction with/without document context."""

import pytest
from septum_api.services.chat_context import ChatContextPayload, build_chat_prompt


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

    assert "You are a helpful assistant." in prompt
    assert "Do NOT claim that information is missing from a document" in prompt
    assert "User question:\nTest query" in prompt
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


def _prompt_for(language) -> str:
    return build_chat_prompt(
        ChatContextPayload(
            sanitized_query="Test query",
            context_chunks=["Chunk body"],
            regulation_names=["GDPR"],
            language=language,
            schema_instruction="",
            placeholder_list_str="",
            output_mode="chat",
        )
    )


@pytest.mark.parametrize("language", ["en", "de"])
def test_respond_in_language_instruction_for_every_known_language(language: str) -> None:
    prompt = _prompt_for(language)

    assert prompt.startswith(
        f"IMPORTANT: The user's query is in {language.upper()} language. "
        f"You MUST respond in the same language ({language.upper()}).\n\n"
    )


@pytest.mark.parametrize("language", [None, ""])
def test_no_language_instruction_when_language_is_unknown(language) -> None:
    prompt = _prompt_for(language)

    assert "IMPORTANT:" not in prompt
    assert "respond in the same language" not in prompt.lower()
