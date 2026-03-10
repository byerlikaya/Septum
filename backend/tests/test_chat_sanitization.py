from __future__ import annotations

"""Tests for chat query sanitization and regulation-aware behaviour."""

from typing import Any

import pytest

from backend.app.models.settings import AppSettings
from backend.app.services.anonymization_map import AnonymizationMap
from backend.app.services.policy_composer import ComposedPolicy
from backend.app.services.sanitizer import PIISanitizer


def test_piisanitizer_policy_construction_does_not_crash() -> None:
    """
    Sanity check: PIISanitizer can be constructed with a minimal ComposedPolicy
    object (without executing .sanitize(), to avoid external IO in tests).
    """

    policy = ComposedPolicy(entity_types=["EMAIL_ADDRESS"], recognizers=[], regulation_ids=[])
    settings = AppSettings(
        id=1,
        llm_provider="anthropic",
        llm_model="claude-3-5-sonnet-latest",
        ollama_base_url="http://localhost:11434",
        ollama_chat_model="llama3.2:3b",
        ollama_deanon_model="llama3.2:3b",
        deanon_enabled=True,
        deanon_strategy="simple",
        require_approval=False,
        show_json_output=False,
        use_presidio_layer=True,
        use_ner_layer=False,
        use_ollama_layer=False,
        chunk_size=800,
        chunk_overlap=200,
        top_k_retrieval=5,
        pdf_chunk_size=1200,
        audio_chunk_size=60,
        spreadsheet_chunk_size=200,
        whisper_model="base",
        image_ocr_languages=["en"],
        extract_embedded_images=True,
        recursive_email_attachments=True,
        default_active_regulations=["gdpr"],
    )
    sanitizer = PIISanitizer(settings=settings, policy=policy)
    anon_map = AnonymizationMap(document_id=0, language="en")
    # Just ensure construction and a no-op call path do not raise.
    assert isinstance(sanitizer, PIISanitizer)
    assert isinstance(anon_map, AnonymizationMap)

