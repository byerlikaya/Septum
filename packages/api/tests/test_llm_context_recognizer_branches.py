"""Characterization of LLMContextRecognizer.analyze (pins behaviour before a refactor).

Ollama is never called: the client functions are replaced in the registry module.
"""

from __future__ import annotations

from typing import Any

import pytest
from septum_api.services.recognizers import registry as registry_module
from septum_api.services.recognizers.registry import (
    LLMContextConfig,
    LLMContextRecognizer,
)


def _recognizer(llm_prompt: str = "find ids") -> LLMContextRecognizer:
    return LLMContextRecognizer(
        LLMContextConfig(name="r", entity_type="CUSTOM_ID", llm_prompt=llm_prompt, context_words=[])
    )


@pytest.fixture
def ollama(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Ollama enabled, returns the items handed to ``items``; records each call."""
    state: dict[str, Any] = {"enabled": True, "items": [], "calls": 0}

    def _call(**kwargs: Any) -> str:
        state["calls"] += 1
        return "raw"

    monkeypatch.setattr(registry_module, "use_ollama_enabled", lambda: state["enabled"])
    monkeypatch.setattr(registry_module, "call_ollama_sync", _call)
    monkeypatch.setattr(registry_module, "extract_json_array", lambda response: state["items"])
    return state


def _spans(results) -> list[tuple[int, int, str, float]]:
    return [(r.start, r.end, r.entity_type, r.score) for r in results]


def test_disabled_ollama_returns_nothing_without_a_call(ollama: dict[str, Any]) -> None:
    ollama["enabled"] = False

    assert _recognizer().analyze("abc") == []
    assert ollama["calls"] == 0


def test_entity_filter_without_overlap_returns_nothing_without_a_call(ollama: dict[str, Any]) -> None:
    assert _recognizer().analyze("abc", entities=["PERSON"]) == []
    assert ollama["calls"] == 0


def test_entity_filter_with_overlap_runs_detection(ollama: dict[str, Any]) -> None:
    ollama["items"] = [{"start": 0, "end": 3, "text": "abc"}]

    assert _spans(_recognizer().analyze("abc", entities=["CUSTOM_ID"])) == [(0, 3, "CUSTOM_ID", 0.8)]


def test_empty_text_returns_nothing_without_a_call(ollama: dict[str, Any]) -> None:
    assert _recognizer().analyze("") == []
    assert ollama["calls"] == 0


def test_empty_prompt_returns_nothing_without_a_call(ollama: dict[str, Any]) -> None:
    assert _recognizer(llm_prompt="").analyze("abc") == []
    assert ollama["calls"] == 0


def test_valid_offsets_are_used_when_the_span_text_matches(ollama: dict[str, Any]) -> None:
    ollama["items"] = [{"start": 0, "end": 3, "text": "abc"}]

    assert _spans(_recognizer().analyze("abc def")) == [(0, 3, "CUSTOM_ID", 0.8)]


def test_offsets_are_stripped_before_comparing(ollama: dict[str, Any]) -> None:
    ollama["items"] = [{"start": 0, "end": 6, "text": " abc "}]

    assert _spans(_recognizer().analyze("  abc ")) == [(0, 6, "CUSTOM_ID", 0.8)]


def test_mismatched_offsets_fall_back_to_the_first_occurrence(ollama: dict[str, Any]) -> None:
    ollama["items"] = [{"start": 0, "end": 3, "text": "def"}]

    assert _spans(_recognizer().analyze("abc def")) == [(4, 7, "CUSTOM_ID", 0.8)]


def test_out_of_range_offsets_fall_back_to_the_first_occurrence(ollama: dict[str, Any]) -> None:
    ollama["items"] = [{"start": 5, "end": 2, "text": "abc"}]

    assert _spans(_recognizer().analyze("abc")) == [(0, 3, "CUSTOM_ID", 0.8)]


def test_end_past_the_text_length_falls_back_to_the_first_occurrence(ollama: dict[str, Any]) -> None:
    ollama["items"] = [{"start": 0, "end": 10, "text": "abc"}]

    assert _spans(_recognizer().analyze("abc")) == [(0, 3, "CUSTOM_ID", 0.8)]


def test_span_text_absent_from_the_input_is_dropped(ollama: dict[str, Any]) -> None:
    ollama["items"] = [{"start": 0, "end": 3, "text": "zzz"}]

    assert _recognizer().analyze("abc") == []


def test_missing_offsets_locate_the_text(ollama: dict[str, Any]) -> None:
    ollama["items"] = [{"text": "def"}]

    assert _spans(_recognizer().analyze("abc def")) == [(4, 7, "CUSTOM_ID", 0.8)]


def test_non_string_span_text_is_compared_as_text(ollama: dict[str, Any]) -> None:
    ollama["items"] = [{"text": 42}]

    assert _spans(_recognizer().analyze("x 42 y")) == [(2, 4, "CUSTOM_ID", 0.8)]


def test_items_without_usable_text_or_dict_shape_are_skipped(ollama: dict[str, Any]) -> None:
    ollama["items"] = ["not a dict", 5, {"start": 0, "end": 3}, {"text": None}, {"text": ""}]

    assert _recognizer().analyze("abc") == []


def test_several_items_keep_their_order(ollama: dict[str, Any]) -> None:
    ollama["items"] = [{"text": "bb"}, {"text": "missing"}, {"start": 0, "end": 1, "text": "a"}]

    assert _spans(_recognizer().analyze("a bb")) == [(2, 4, "CUSTOM_ID", 0.8), (0, 1, "CUSTOM_ID", 0.8)]
