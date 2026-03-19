from __future__ import annotations

"""Structured payload for RAG-based chat context.

This module defines the internal data model used to pass document chunks,
regulation metadata, and other context parameters between the chat router
and the LLM/prompt layer. The payload is intentionally generic and does
not couple to specific LLM providers.
"""

from dataclasses import dataclass
from typing import List, Literal, Optional


@dataclass
class ChatContextPayload:
    """Structured RAG context for building a chat prompt."""

    sanitized_query: str
    context_chunks: List[str]
    regulation_names: List[str]
    language: str
    schema_instruction: str
    placeholder_list_str: str
    output_mode: Literal["chat", "json"]

    def has_context(self) -> bool:
        """Return whether this payload includes any document context."""
        return bool(self.context_chunks)


def build_chat_prompt(payload: ChatContextPayload) -> str:
    """Build the final chat prompt from a :class:`ChatContextPayload`.

    This helper centralises the logic for constructing the RAG prompt so
    that both Cloud and Desktop Assistant flows use the exact same prompt
    generation without duplicating prompt assembly code.
    """
    from .prompts import PromptCatalog

    context_text = ""
    if payload.context_chunks:
        lines: List[str] = []
        for idx, chunk in enumerate(payload.context_chunks, start=1):
            lines.append(f"Chunk {idx}:\n{chunk}")
        context_text = "\n\n".join(lines)

    regulations_str = ", ".join(payload.regulation_names) if payload.regulation_names else "None"

    output_instruction = ""
    if payload.output_mode == "json":
        output_instruction = PromptCatalog.json_output_instruction()

    return PromptCatalog.chat_user_prompt(
        language=payload.language,
        regulations_str=regulations_str,
        sanitized_query=payload.sanitized_query,
        context_text=context_text,
        schema_instruction=payload.schema_instruction,
        placeholder_list_str=payload.placeholder_list_str,
        output_instruction=output_instruction,
    )
