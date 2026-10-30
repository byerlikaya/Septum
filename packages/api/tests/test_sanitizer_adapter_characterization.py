"""Characterization tests for :class:`OllamaSemanticAdapter`.

These pin the observable behaviour of the four Ollama-backed detection
hooks (returned spans, offsets, entity types, scores, anon map mutations,
log lines and Ollama call arguments) so their internals can be
restructured without changing what the PII pipeline produces. Every
Ollama call is mocked; no network is touched.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from septum_api.models.settings import AppSettings
from septum_api.services import sanitizer as sanitizer_module
from septum_api.services.sanitizer import (
    AnonymizationMap,
    DetectedSpan,
    OllamaSemanticAdapter,
)

_LOGGER = "septum_api.services.sanitizer"


class _OllamaStub:
    """Records every ``call_ollama_sync`` invocation and returns a canned reply."""

    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.response


@pytest.fixture
def adapter() -> OllamaSemanticAdapter:
    """Adapter wired to a settings row with a distinctive URL and model id."""
    settings = AppSettings(
        id=1,
        ollama_base_url="http://ollama.test:11434",
        ollama_deanon_model="deanon-model:1",
    )
    return OllamaSemanticAdapter(settings=settings)


def _stub(monkeypatch: pytest.MonkeyPatch, response: Any) -> _OllamaStub:
    stub = _OllamaStub(response)
    monkeypatch.setattr(sanitizer_module, "call_ollama_sync", stub)
    return stub


def _triples(spans: list[DetectedSpan]) -> list[tuple[int, int, str, float]]:
    return [(s.start, s.end, s.entity_type, s.score) for s in spans]


def _messages(caplog: pytest.LogCaptureFixture) -> list[tuple[int, str]]:
    return [(r.levelno, r.getMessage()) for r in caplog.records if r.name == _LOGGER]


# ---------------------------------------------------------------- validate_candidates


def _candidates() -> list[DetectedSpan]:
    return [
        DetectedSpan(start=0, end=5, entity_type="PERSON_NAME", score=0.9),
        DetectedSpan(start=9, end=15, entity_type="LOCATION", score=0.7),
        DetectedSpan(start=19, end=23, entity_type="ORGANIZATION_NAME", score=0.6),
    ]


_VALIDATE_TEXT = "Alice in London at ACME today"


def test_validate_no_candidates_returns_empty_without_call(adapter, monkeypatch):
    stub = _stub(monkeypatch, "[]")
    assert adapter.validate_candidates(
        text=_VALIDATE_TEXT, candidate_spans=[], language="en", regulation_context="r"
    ) == []
    assert stub.calls == []


def test_validate_empty_text_returns_copy_without_call(adapter, monkeypatch):
    stub = _stub(monkeypatch, "[]")
    cands = _candidates()
    out = adapter.validate_candidates(
        text="", candidate_spans=cands, language="en", regulation_context="r"
    )
    assert out == cands and out is not cands
    assert stub.calls == []


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (None, "Ollama validation returned empty response; keeping all candidates"),
        ("   ", "Ollama validation returned empty response; keeping all candidates"),
        ("no json here", "Ollama validation parsed no spans; keeping all candidates"),
        ("[]", "Ollama validation parsed no spans; keeping all candidates"),
        (
            '[{"text": "x", "entity_type": "PERSON_NAME", "start": 1, "end": 5}]',
            "Ollama validation matched no spans; keeping all candidates",
        ),
    ],
)
def test_validate_fallbacks_keep_all_candidates(adapter, monkeypatch, caplog, response, message):
    _stub(monkeypatch, response)
    cands = _candidates()
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        out = adapter.validate_candidates(
            text=_VALIDATE_TEXT, candidate_spans=cands, language="en", regulation_context="r"
        )
    assert out == cands and out is not cands
    assert _messages(caplog) == [(logging.WARNING, message)]


def test_validate_keeps_matched_originals_in_item_order(adapter, monkeypatch, caplog):
    response = (
        '[{"text": "ACME", "entity_type": "ORGANIZATION_NAME", "start": 19, "end": 23},'
        ' "not-a-dict",'
        ' {"text": "London", "entity_type": "LOCATION", "start": 9},'
        ' {"text": null, "entity_type": "LOCATION", "start": 9, "end": 15},'
        ' {"text": "London", "entity_type": "PERSON_NAME", "start": 9, "end": 15},'
        ' {"text": "anything", "entity_type": "PERSON_NAME", "start": 0, "end": 5},'
        ' {"text": "ACME", "entity_type": "ORGANIZATION_NAME", "start": 19, "end": 23}]'
    )
    stub = _stub(monkeypatch, response)
    cands = _candidates()
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        out = adapter.validate_candidates(
            text=_VALIDATE_TEXT, candidate_spans=cands, language="tr", regulation_context="RULES"
        )
    assert [id(s) for s in out] == [id(cands[2]), id(cands[0]), id(cands[2])]
    assert _messages(caplog) == []
    assert len(stub.calls) == 1
    call = stub.calls[0]
    assert call["base_url"] == "http://ollama.test:11434"
    assert call["model"] == "deanon-model:1"
    assert set(call) == {"prompt", "base_url", "model"}


def test_validate_prompt_receives_candidate_dicts(adapter, monkeypatch):
    captured: dict[str, Any] = {}

    def _prompt(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "PROMPT"

    monkeypatch.setattr(sanitizer_module.PromptCatalog, "pii_validation_prompt", _prompt)
    stub = _stub(monkeypatch, "")
    adapter.validate_candidates(
        text=_VALIDATE_TEXT, candidate_spans=_candidates(), language="de", regulation_context="CTX"
    )
    assert captured == {
        "text": _VALIDATE_TEXT,
        "candidate_spans": [
            {"text": "Alice", "entity_type": "PERSON_NAME", "start": 0, "end": 5, "score": 0.9},
            {"text": "London", "entity_type": "LOCATION", "start": 9, "end": 15, "score": 0.7},
            {"text": "ACME", "entity_type": "ORGANIZATION_NAME", "start": 19, "end": 23, "score": 0.6},
        ],
        "language": "de",
        "regulation_rules": "CTX",
    }
    assert stub.calls[0]["prompt"] == "PROMPT"


# ---------------------------------------------------------------- detect_aliases


def test_aliases_empty_text_returns_empty_without_call(adapter, monkeypatch):
    stub = _stub(monkeypatch, "[]")
    assert adapter.detect_aliases(normalized_text="") == []
    assert stub.calls == []


@pytest.mark.parametrize(
    ("text", "numeric", "total"),
    [
        ("Total 100\nTax $ 5\nName John Smith", 2, 3),
        ("a 1\nb 2\nplain words here\nmore plain words\nyet more words", 2, 5),
        ("Price 10€\n\n   \nhello there friend", 1, 2),
        ("50%", 1, 1),
    ],
)
def test_aliases_skip_numeric_heavy_text(adapter, monkeypatch, caplog, text, numeric, total):
    stub = _stub(monkeypatch, "[]")
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        assert adapter.detect_aliases(normalized_text=text) == []
    assert stub.calls == []
    assert _messages(caplog) == [
        (
            logging.DEBUG,
            "Skipping Ollama alias layer for numeric-heavy structured text "
            f"({numeric}/{total} lines classified as numeric-like).",
        )
    ]


@pytest.mark.parametrize(
    "text",
    [
        "a 1\nplain words here\nmore plain words",
        "one two three 4\nfive six",
        "\n\n  \n",
    ],
)
def test_aliases_below_numeric_threshold_calls_ollama(adapter, monkeypatch, text):
    stub = _stub(monkeypatch, "[]")
    assert adapter.detect_aliases(normalized_text=text) == []
    assert len(stub.calls) == 1


def test_aliases_empty_response_warns_and_returns_empty(adapter, monkeypatch, caplog):
    _stub(monkeypatch, "  ")
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        assert adapter.detect_aliases(normalized_text="Hello Bob there") == []
    assert _messages(caplog) == [
        (logging.WARNING, "Layer 3 Ollama returned empty response"),
        (logging.DEBUG, "Layer 3 Ollama parsed 0 JSON items"),
    ]


def test_aliases_span_filtering_and_offsets(adapter, monkeypatch, caplog):
    text = "Meet Bobby and JOHNNY near Annabelle; Bobcat sees Al. Kim met Kim."
    response = (
        "["
        '"x",'
        '{"text": null, "type": "ALIAS"},'
        '{"text": "", "type": "ALIAS"},'
        '{"text": " johnny ", "type": "alias"},'
        '{"text": "Bobby"},'
        '{"text": "Annabelle", "entity_type": "person name"},'
        '{"text": "Annabelle", "entity_type": "LOCATION"},'
        '{"text": "Al", "type": "ALIAS"},'
        '{"text": "Zed", "type": "ALIAS"},'
        '{"text": "Bobc", "type": "ALIAS"},'
        '{"text": "Kim", "type": "FIRST_NAME"},'
        '{"text": "near", "entity_type": "LAST_NAME", "type": "LOCATION"}'
        "]"
    )
    stub = _stub(monkeypatch, response)
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        spans = adapter.detect_aliases(normalized_text=text)
    assert _triples(spans) == [
        (15, 21, "ALIAS", 0.85),
        (5, 10, "ALIAS", 0.85),
        (27, 36, "PERSON_NAME", 0.85),
        (22, 26, "LAST_NAME", 0.85),
    ]
    assert _messages(caplog) == [(logging.DEBUG, "Layer 3 Ollama parsed 12 JSON items")]
    assert stub.calls[0]["base_url"] == "http://ollama.test:11434"
    assert stub.calls[0]["model"] == "deanon-model:1"


def test_aliases_offsets_follow_lowercased_text(adapter, monkeypatch):
    _stub(monkeypatch, '[{"text": "Bobby", "type": "ALIAS"}]')
    assert adapter.detect_aliases(normalized_text="İİ Bobby here") == []
    assert _triples(adapter.detect_aliases(normalized_text="ii Bobby here")) == [
        (3, 8, "ALIAS", 0.85)
    ]


def test_aliases_prompt_built_from_text(adapter, monkeypatch):
    seen: list[str] = []

    def _prompt(text: str) -> str:
        seen.append(text)
        return "ALIAS-PROMPT"

    monkeypatch.setattr(sanitizer_module.PromptCatalog, "sanitizer_alias_layer", _prompt)
    stub = _stub(monkeypatch, "[]")
    adapter.detect_aliases(normalized_text="Hello Bob there")
    assert seen == ["Hello Bob there"]
    assert stub.calls[0]["prompt"] == "ALIAS-PROMPT"


# ---------------------------------------------------------------- detect_semantic


_SEMANTIC_TYPES = ["DIAGNOSIS", "BIOMETRIC_ID", "RELIGION", "MEDICATION"]


@pytest.mark.parametrize("response", [None, "", "   "])
def test_semantic_empty_response_returns_empty(adapter, monkeypatch, caplog, response):
    _stub(monkeypatch, response)
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        assert adapter.detect_semantic(normalized_text="text", entity_types=_SEMANTIC_TYPES) == []
    assert _messages(caplog) == []


def test_semantic_span_filtering_and_offsets(adapter, monkeypatch, caplog):
    text = (
        "Patient has asthma attack. Print FP-1234 kept, fp-5678 too. Faith BUDDHISM noted. "
        "2. Notes here. Takes aspirin daily, aspirin again. Scan ABCD done."
    )
    response = (
        "["
        "1,"
        '{"text": "asthma attack", "type": "diagnosis"},'
        '{"text": "", "type": "DIAGNOSIS"},'
        '{"text": "asthma", "type": "LOCATION"},'
        '{"text": "has", "type": "DIAGNOSIS"},'
        '{"text": "  FP-1234 ", "type": "biometric id"},'
        '{"text": "fp-5678", "type": "BIOMETRIC_ID"},'
        '{"text": "ABCD", "type": "BIOMETRIC_ID"},'
        '{"text": "BUDDHISM", "type": "RELIGION"},'
        '{"text": "2. Notes", "type": "MEDICATION"},'
        '{"text": "aspirin", "type": "MEDICATION"},'
        '{"text": "missing", "type": "MEDICATION"},'
        '{"text": "atient", "type": "DIAGNOSIS"},'
        '{"text": "Faith", "type": "RELIGION"}'
        "]"
    )
    stub = _stub(monkeypatch, response)
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        spans = adapter.detect_semantic(normalized_text=text, entity_types=_SEMANTIC_TYPES)
    assert _triples(spans) == [
        (12, 25, "DIAGNOSIS", 0.80),
        (47, 54, "BIOMETRIC_ID", 0.80),
        (60, 65, "RELIGION", 0.80),
    ]
    assert _messages(caplog) == []
    assert stub.calls[0]["base_url"] == "http://ollama.test:11434"
    assert stub.calls[0]["model"] == "deanon-model:1"


def test_semantic_non_heading_prone_type_keeps_caps(adapter, monkeypatch):
    _stub(monkeypatch, '[{"text": "ACME CORP", "type": "EMPLOYER"}]')
    spans = adapter.detect_semantic(
        normalized_text="Works at ACME CORP now", entity_types=["EMPLOYER"]
    )
    assert _triples(spans) == [(9, 18, "EMPLOYER", 0.80)]


def test_semantic_prompt_arguments(adapter, monkeypatch):
    seen: list[tuple[str, list[str]]] = []

    def _prompt(text: str, types: list[str]) -> str:
        seen.append((text, types))
        return "SEM-PROMPT"

    monkeypatch.setattr(sanitizer_module.PromptCatalog, "semantic_pii_detection", _prompt)
    stub = _stub(monkeypatch, "[]")
    adapter.detect_semantic(normalized_text="abc", entity_types=["DIAGNOSIS"])
    assert seen == [("abc", ["DIAGNOSIS"])]
    assert stub.calls[0]["prompt"] == "SEM-PROMPT"


# ---------------------------------------------------------------- resolve_coreference


def _person_map() -> AnonymizationMap:
    anon = AnonymizationMap(document_id=42, language="en")
    anon.entity_map.update({
        "Ahmet Yilmaz": "[PERSON_NAME_1]",
        "Ayse": "[FIRST_NAME_1]",
        "London": "[LOCATION_1]",
    })
    return anon


def test_coreference_without_person_entities_skips_call(adapter, monkeypatch):
    stub = _stub(monkeypatch, "[]")
    anon = AnonymizationMap(document_id=1)
    anon.entity_map["London"] = "[LOCATION_1]"
    assert adapter.resolve_coreference(normalized_text="t", anon_map=anon, language="en") == 0
    assert stub.calls == []


@pytest.mark.parametrize("response", [None, "", "  "])
def test_coreference_empty_response_returns_zero(adapter, monkeypatch, response):
    _stub(monkeypatch, response)
    anon = _person_map()
    assert adapter.resolve_coreference(normalized_text="t", anon_map=anon, language="en") == 0
    assert anon.blocklist == set()
    assert anon.token_to_placeholder == {}


def test_coreference_resolves_pronouns(adapter, monkeypatch, caplog):
    response = (
        "["
        '{"text": "He", "refers_to": "Ahmet Yilmaz"},'
        '{"text": " SHE ", "refers_to": " ayse "},'
        '{"text": "", "refers_to": "Ayse"},'
        '{"text": "they", "refers_to": ""},'
        '{"text": "it", "refers_to": "Nobody"},'
        '{"text": "I", "refers_to": "Ayse"},'
        '{"text": "The", "refers_to": "Ayse"},'
        '{"text": "Him", "refers_to": "London"},'
        '{"refers_to": "Ayse"}'
        "]"
    )
    stub = _stub(monkeypatch, response)
    anon = _person_map()
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        count = adapter.resolve_coreference(
            normalized_text="He said she would.", anon_map=anon, language="en"
        )
    assert count == 3
    assert anon.blocklist == {"he", "she", "him"}
    assert anon.token_to_placeholder == {
        "he": "[PERSON_NAME_1]",
        "she": "[FIRST_NAME_1]",
        "him": "[LOCATION_1]",
    }
    assert _messages(caplog) == [
        (logging.DEBUG, "Pronoun coreference resolved 3 pronouns for document_id=42")
    ]
    assert stub.calls[0]["base_url"] == "http://ollama.test:11434"
    assert stub.calls[0]["model"] == "deanon-model:1"


def test_coreference_prompt_lists_person_entities_only(adapter, monkeypatch):
    captured: dict[str, Any] = {}

    def _prompt(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "COREF"

    monkeypatch.setattr(
        sanitizer_module.PromptCatalog, "pronoun_coreference_resolution", _prompt
    )
    stub = _stub(monkeypatch, "[]")
    anon = _person_map()
    with_logs = adapter.resolve_coreference(normalized_text="txt", anon_map=anon, language="tr")
    assert with_logs == 0
    assert captured == {
        "normalized_text": "txt",
        "known_persons": [
            {"name": "Ahmet Yilmaz", "placeholder": "[PERSON_NAME_1]"},
            {"name": "Ayse", "placeholder": "[FIRST_NAME_1]"},
        ],
        "language": "tr",
    }
    assert stub.calls[0]["prompt"] == "COREF"


def test_coreference_no_resolution_logs_nothing(adapter, monkeypatch, caplog):
    _stub(monkeypatch, '[{"text": "it", "refers_to": "Nobody"}]')
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        assert adapter.resolve_coreference(
            normalized_text="t", anon_map=_person_map(), language="en"
        ) == 0
    assert _messages(caplog) == []


def test_coreference_non_dict_item_raises(adapter, monkeypatch):
    _stub(monkeypatch, '["He"]')
    with pytest.raises(AttributeError):
        adapter.resolve_coreference(normalized_text="t", anon_map=_person_map(), language="en")
