"""NER runs on text of any length; only the semantic alias layer waits for enough words."""

from __future__ import annotations

import pytest
from ner_fakes import FakePipeline, StubRegistry
from septum_core import SeptumEngine
from septum_core.anonymization_map import AnonymizationMap
from septum_core.config import SeptumCoreConfig
from septum_core.detector import _MIN_WORDS_FOR_SEMANTIC_ALIAS, Detector
from septum_core.ner_model_registry import _WindowedNERPipeline

SHORT_TEXTS = (
    ("Ahmet Yılmaz kim?", "tr", "Ahmet Yılmaz"),
    ("Ali Veli", "tr", "Ali Veli"),
    ("张伟在哪", "zh", "张伟"),
    ("张伟", "zh", "张伟"),
    ("김민준 어디?", "ko", "김민준"),
)

_NER_ONLY = SeptumCoreConfig(use_presidio_layer=False, use_ner_layer=True)


class _AliasPort:
    def __init__(self) -> None:
        self.alias_calls = 0

    def is_enabled(self) -> bool:
        return True

    def detect_aliases(self, *, normalized_text):
        self.alias_calls += 1
        return []


def _registry(name: str) -> tuple[StubRegistry, FakePipeline]:
    fake = FakePipeline(emit={name: "PER"})
    return StubRegistry(_WindowedNERPipeline(fake, None)), fake


def _alias_calls(text: str) -> int:
    port = _AliasPort()
    detector = Detector(
        SeptumCoreConfig(
            use_presidio_layer=False,
            use_ner_layer=False,
            use_semantic_detection=True,
            use_semantic_contextual_detection=False,
        ),
        semantic_port=port,
    )
    detector.sanitize(text, "en", AnonymizationMap(document_id=0, language="en"))
    return port.alias_calls


@pytest.mark.parametrize("text, language, name", SHORT_TEXTS)
def test_short_text_reaches_ner_through_sanitize(text: str, language: str, name: str) -> None:
    registry, fake = _registry(name)
    detector = Detector(_NER_ONLY, ner_registry=registry)

    result = detector.sanitize(text, language, AnonymizationMap(document_id=0, language=language))

    assert fake.chunks
    assert [text[s.start:s.end] for s in result.detected_spans] == [name]


@pytest.mark.parametrize("text, language, name", SHORT_TEXTS)
def test_short_text_reaches_ner_through_engine_mask(text: str, language: str, name: str) -> None:
    registry, fake = _registry(name)
    engine = SeptumEngine(regulations=["gdpr"], config=_NER_ONLY, ner_registry=registry)

    result = engine.mask(text, language=language)

    assert fake.chunks
    assert result.entity_count == 1
    assert name not in result.masked_text
    assert engine.unmask(result.masked_text, result.session_id) == text


@pytest.mark.parametrize("text", ("王", "a", " 王 "))
def test_one_character_text_still_calls_ner(text: str) -> None:
    registry, fake = _registry("x")
    detector = Detector(_NER_ONLY, ner_registry=registry)

    detector.sanitize(text, "zh", AnonymizationMap(document_id=0, language="zh"))

    assert fake.chunks


@pytest.mark.parametrize("text", ("", " ", "  \n\t "))
def test_blank_text_does_not_call_ner(text: str) -> None:
    registry, fake = _registry("x")
    detector = Detector(_NER_ONLY, ner_registry=registry)

    result = detector.sanitize(text, "en", AnonymizationMap(document_id=0, language="en"))

    assert fake.chunks == []
    assert result.sanitized_text == text


def _words(word: str, count: int, sep: str = " ") -> str:
    return sep.join([word] * count)


@pytest.mark.parametrize(
    "text, runs",
    (
        (_words("word", _MIN_WORDS_FOR_SEMANTIC_ALIAS - 1), False),
        (_words("word", _MIN_WORDS_FOR_SEMANTIC_ALIAS), True),
        (_words("北", _MIN_WORDS_FOR_SEMANTIC_ALIAS - 1, ""), False),
        (_words("北", _MIN_WORDS_FOR_SEMANTIC_ALIAS, ""), True),
        (_words("서울", _MIN_WORDS_FOR_SEMANTIC_ALIAS - 1), False),
        (_words("서울", _MIN_WORDS_FOR_SEMANTIC_ALIAS), True),
    ),
)
def test_semantic_alias_floor_counts_words(text: str, runs: bool) -> None:
    assert _alias_calls(text) == int(runs)


# Literal thresholds: the cases above derive their lengths from the constant,
# so they cannot notice the constant itself drifting.
@pytest.mark.parametrize(
    "text, runs",
    (
        ("one two three four five six seven eight nine ten eleven", False),
        ("one two three four five six seven eight nine ten eleven twelve", True),
        ("x" * 200, False),
        ("北" * 11, False),
        ("北" * 12, True),
        (" ".join(["서울"] * 11), False),
        (" ".join(["서울"] * 12), True),
    ),
)
def test_semantic_alias_floor_is_twelve_words(text: str, runs: bool) -> None:
    assert _alias_calls(text) == int(runs)
