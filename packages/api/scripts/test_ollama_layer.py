#!/usr/bin/env python3
"""Quick test for sanitizer Layer 3 (Ollama PII detection). Run from backend: python scripts/test_ollama_layer.py"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure backend/app is importable when run as python scripts/test_ollama_layer.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Enable Ollama and debug logs when not set
if os.getenv("USE_OLLAMA") is None:
    os.environ["USE_OLLAMA"] = "true"
if os.getenv("LOG_LEVEL") is None:
    os.environ["LOG_LEVEL"] = "DEBUG"

import logging
logging.basicConfig(level=logging.DEBUG)

from septum_api.config import get_settings
from septum_api.services.anonymization_map import AnonymizationMap
from septum_api.services.sanitizer import PIISanitizer


def main() -> None:
    settings = get_settings()
    settings.use_ollama_layer = True
    sanitizer = PIISanitizer(settings=settings)
    anon_map = AnonymizationMap(document_id=1, language="en")

    text = (
        "The client referred to as The Big Fish signed on Tuesday. "
        "His driver lives at the corner house."
    )
    result = sanitizer.sanitize(text, language="en", anon_map=anon_map)
    print("Sanitized:", result.sanitized_text)
    print("Entities found:", result.entity_count)

    # Expected: "The Big Fish" as single placeholder [ALIAS_1] or [NICKNAME_1], Tuesday -> [DATE_TIME_1]
    assert "referred to as [" in result.sanitized_text, "Alias should be one placeholder"
    assert "]" in result.sanitized_text and "signed on [" in result.sanitized_text, "Date placeholder expected"
    assert "The [" not in result.sanitized_text, "Leading 'The' must be part of alias span, not left outside"
    assert "[BLOCKED]" not in result.sanitized_text, "No [BLOCKED] placeholder allowed"
    assert result.entity_count >= 2, "Expect at least 2 entities (e.g. alias + date)"


if __name__ == "__main__":
    main()
