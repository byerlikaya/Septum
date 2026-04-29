from __future__ import annotations

"""Regression tests for keyword-list custom recognizers.

A bug in ``RecognizerRegistry._build_keyword_recognizer`` shipped a
literal ``\\b`` (backslash + ``b``) instead of the regex word boundary,
silently turning every user-defined keyword recognizer into a no-op.
This file pins the working behavior.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from septum_core.recognizers.registry import RecognizerRegistry


@dataclass
class _FakeKeywordRecognizer:
    id: int = 1
    name: str = "internal_codenames"
    entity_type: str = "CODENAME"
    detection_method: str = "keyword_list"
    pattern: Optional[str] = None
    keywords: Optional[List[str]] = None
    llm_prompt: Optional[str] = None
    context_words: List[str] = field(default_factory=list)
    placeholder_label: str = "CODENAME"
    is_active: bool = True


def test_keyword_recognizer_matches_keywords_in_text() -> None:
    custom = _FakeKeywordRecognizer(keywords=["BlueLagoon", "FrostStar"])
    recognizer = RecognizerRegistry._build_keyword_recognizer(custom)

    text = "The BlueLagoon project ships before FrostStar."
    matches = recognizer.analyze(text=text, entities=["CODENAME"], nlp_artifacts=None)

    assert len(matches) == 2
    matched_strings = sorted(text[m.start:m.end] for m in matches)
    assert matched_strings == ["BlueLagoon", "FrostStar"]


def test_keyword_recognizer_respects_word_boundary() -> None:
    custom = _FakeKeywordRecognizer(keywords=["cat"])
    recognizer = RecognizerRegistry._build_keyword_recognizer(custom)

    text = "The cat sat on the catalog."
    matches = recognizer.analyze(text=text, entities=["CODENAME"], nlp_artifacts=None)

    assert len(matches) == 1
    assert text[matches[0].start:matches[0].end] == "cat"
