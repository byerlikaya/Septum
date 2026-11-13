"""Presidio's spaCy LOCATION results survive the NER layer for languages the spaCy engine has a model for."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from septum_core import detector as detector_module
from septum_core.config import SeptumCoreConfig
from septum_core.detector import Detector
from septum_core.document_windowing import WindowingPolicy
from septum_core.language_segments import Segment
from test_segment_detection import make_detector

_CONFIGURED = next(iter(detector_module._SPACY_MODELS))
_UNCONFIGURED = "xx"
_NER_ON = SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=True)


def _filter_only(use_ner: bool = True) -> Detector:
    detector = Detector.__new__(Detector)
    detector._config = SeptumCoreConfig(use_ner_layer=use_ner)
    return detector


def _result(entity_type: str, start: int, end: int, score: float = 0.85) -> SimpleNamespace:
    return SimpleNamespace(entity_type=entity_type, start=start, end=end, score=score)


def _old_path(detector: Detector, results: list, text: str) -> list:
    """The filter before the change, for the entity types this test uses."""
    return [
        r
        for r in results
        if not (r.entity_type == "LOCATION" and detector._config.use_ner_layer)
        and not (
            detector._config.use_ner_layer and r.entity_type == "PERSON_NAME" and r.score < detector_module._PRESIDIO_PERSON_MIN_SCORE_WHEN_NER_ENABLED
        )
        and not _old_drops_phone(r, text)
    ]


def _old_drops_phone(r: SimpleNamespace, text: str) -> bool:
    if r.entity_type != "PHONE_NUMBER":
        return False
    span = text[r.start : r.end]
    return ("." in span or "/" in span) and sum(ch.isdigit() for ch in span) <= 8


def test_location_of_a_configured_language_is_kept_with_the_ner_layer_on() -> None:
    results = [_result("LOCATION", 0, 5)]

    kept = _filter_only()._filter_presidio_results(results, "Paris x", lambda _: _CONFIGURED)

    assert kept == results


def test_region_tagged_configured_language_counts_as_configured() -> None:
    results = [_result("LOCATION", 0, 5)]

    kept = _filter_only()._filter_presidio_results(
        results, "Paris x", lambda _: f"{_CONFIGURED}-GB"
    )

    assert kept == results


def test_location_of_a_language_without_a_spacy_model_is_dropped() -> None:
    results = [_result("LOCATION", 0, 5)]

    kept = _filter_only()._filter_presidio_results(results, "Paris x", lambda _: _UNCONFIGURED)

    assert kept == []


def test_location_is_dropped_when_no_language_is_given() -> None:
    results = [_result("LOCATION", 0, 5)]

    assert _filter_only()._filter_presidio_results(results, "Paris x") == []


def test_location_is_kept_with_the_ner_layer_off_whatever_the_language() -> None:
    results = [_result("LOCATION", 0, 5)]

    kept = _filter_only(use_ner=False)._filter_presidio_results(
        results, "Paris x", lambda _: _UNCONFIGURED
    )

    assert kept == results


def test_other_entity_types_are_unchanged_by_the_language() -> None:
    detector = _filter_only()
    results = [
        _result("PERSON_NAME", 0, 5, score=0.4),
        _result("PERSON_NAME", 0, 5, score=0.9),
        _result("ORGANIZATION_NAME", 0, 5),
    ]

    for language in (_CONFIGURED, _UNCONFIGURED):
        kept = detector._filter_presidio_results(results, "Paris x", lambda _, l=language: l)
        assert kept == results[1:]


def test_language_is_judged_at_each_result_start() -> None:
    text = "Paris and Ankara"
    results = [_result("LOCATION", 0, 5), _result("LOCATION", 10, 16)]

    kept = _filter_only()._filter_presidio_results(
        results, text, lambda pos: _CONFIGURED if pos < 10 else _UNCONFIGURED
    )

    assert kept == results[:1]


@pytest.mark.parametrize("use_ner", [True, False])
@pytest.mark.parametrize("language", [_CONFIGURED, _UNCONFIGURED])
def test_the_old_result_is_a_subset_of_the_new_one(use_ner: bool, language: str) -> None:
    detector = _filter_only(use_ner)
    text = "Paris and Ankara met Jane Doe, +1 555 123 4567 ref 12.34.56"
    dotted = text.index("12.34.56")
    results = [
        _result("LOCATION", 0, 5),
        _result("LOCATION", 10, 16),
        _result("PERSON_NAME", 21, 29, 0.4),
        _result("PERSON_NAME", 21, 29, 0.9),
        _result("PHONE_NUMBER", 31, 46),
        _result("PHONE_NUMBER", 31, 36),
        _result("PHONE_NUMBER", dotted, dotted + 8),
    ]

    old = _old_path(detector, results, text)
    new = detector._filter_presidio_results(results, text, lambda _: language)

    assert results[-1] not in old and results[-1] not in new

    assert all(r in new for r in old)
    assert all(r.entity_type == "LOCATION" for r in new if r not in old)


def _detector_with_presidio(results: list) -> Detector:
    detector, _ = make_detector({}, config=_NER_ON)
    detector._analyzer.analyze = lambda **kwargs: results
    return detector


def test_detect_keeps_the_spacy_location_of_a_configured_language() -> None:
    text = "We flew to Paris last week for the review."
    start = text.index("Paris")
    detector = _detector_with_presidio([_result("LOCATION", start, start + 5)])

    result = detector.detect(text, _CONFIGURED)

    assert any(s.entity_type == "LOCATION" and s.start == start for s in result.spans)


def test_detect_drops_the_spacy_location_of_a_language_without_a_model() -> None:
    text = "We flew to Paris last week for the review."
    start = text.index("Paris")
    detector = _detector_with_presidio([_result("LOCATION", start, start + 5)])

    result = detector.detect(text, _UNCONFIGURED)

    assert not any(s.entity_type == "LOCATION" for s in result.spans)


def _locations_by_text():
    def analyze(**kwargs):
        text = kwargs["text"]
        return [
            _result("LOCATION", at, at + len(word))
            for word in ("Paris", "Ankara")
            for at in _occurrences(text, word)
        ]

    return analyze


def _occurrences(text: str, word: str) -> list[int]:
    found, at = [], text.find(word)
    while at != -1:
        found.append(at)
        at = text.find(word, at + 1)
    return found


def _location_starts(result) -> set[int]:
    return {s.start for s in result.spans if s.entity_type == "LOCATION"}


def test_mixed_language_text_keeps_the_location_of_the_configured_segment_when_it_is_the_dominant() -> None:
    first = "We flew to Paris last week for the review of the budget. "
    second = "Komite yillik butceyi Ankara icinde inceledi ve karar verdi. "
    text = first + second
    cut = len(first)
    detector = _detector_with_presidio(
        [_result("LOCATION", text.index("Paris"), text.index("Paris") + 5),
         _result("LOCATION", text.index("Ankara"), text.index("Ankara") + 6)]
    )

    result = detector.detect(
        text, _CONFIGURED, segments=[Segment(0, cut, _CONFIGURED), Segment(cut, len(text), _UNCONFIGURED)]
    )

    assert _location_starts(result) == {text.index("Paris")}


def test_mixed_language_text_keeps_the_location_of_the_configured_segment_when_it_is_the_minority() -> None:
    first = "Komite yillik butceyi Ankara icinde inceledi ve karar verdi. "
    second = "We flew to Paris last week for the review of the budget. "
    text = first + second
    cut = len(first)
    detector = _detector_with_presidio(
        [_result("LOCATION", text.index("Paris"), text.index("Paris") + 5),
         _result("LOCATION", text.index("Ankara"), text.index("Ankara") + 6)]
    )

    result = detector.detect(
        text, _UNCONFIGURED, segments=[Segment(0, cut, _UNCONFIGURED), Segment(cut, len(text), _CONFIGURED)]
    )

    assert _location_starts(result) == {text.index("Paris")}


def test_location_starting_exactly_at_a_segment_cut_belongs_to_the_segment_that_starts_there() -> None:
    first = "We flew in last week for the review of the budget. "
    second = "Paris was the venue of the review of the annual budget. "
    text = first + second
    cut = len(first)
    detector = _detector_with_presidio([_result("LOCATION", cut, cut + 5)])

    into_configured = detector.detect(
        text, _UNCONFIGURED, segments=[Segment(0, cut, _UNCONFIGURED), Segment(cut, len(text), _CONFIGURED)]
    )
    into_unconfigured = detector.detect(
        text, _CONFIGURED, segments=[Segment(0, cut, _CONFIGURED), Segment(cut, len(text), _UNCONFIGURED)]
    )

    assert _location_starts(into_configured) == {cut}
    assert _location_starts(into_unconfigured) == set()


@pytest.mark.parametrize(
    ("tag", "kept"),
    [
        ("", False),
        ("und", False),
        (None, False),
        (f"{_CONFIGURED}_GB", True),
        (_CONFIGURED.upper(), True),
        (f" {_CONFIGURED} ", True),
        (f"{_CONFIGURED}-Latn-US", True),
    ],
)
def test_language_tag_forms_are_canonicalised_before_the_model_lookup(tag, kept: bool) -> None:
    results = [_result("LOCATION", 0, 5)]

    got = _filter_only()._filter_presidio_results(results, "Paris x", lambda _: tag)

    assert got == (results if kept else [])


_WINDOWED = WindowingPolicy(max_whole_chars=300, window_chars=1000, overlap_chars=100)


def _long_mixed_text() -> tuple[str, int]:
    configured = "The committee reviewed the annual budget in detail. " * 13
    unconfigured = "Komite yillik butceyi ayrintili olarak inceledi. " * 16
    configured = configured[:-40] + "Paris hosted the whole review of it. "
    unconfigured = "Ankara hosted the whole review of it. " + unconfigured
    return configured + unconfigured, len(configured)


def test_windowed_detection_judges_a_location_by_the_clipped_segment_of_its_window() -> None:
    text, cut = _long_mixed_text()
    text = text + "Ankara closed it. "
    detector, _ = make_detector({}, config=_NER_ON)
    detector._analyzer.analyze = _locations_by_text()
    segments = [Segment(0, cut, _CONFIGURED), Segment(cut, len(text), _UNCONFIGURED)]

    result = detector.detect(text, _CONFIGURED, windowing=_WINDOWED, segments=segments)

    assert len(_occurrences(text, "Ankara")) == 2
    assert _location_starts(result) == {text.index("Paris")}


def test_windowed_detection_keeps_a_configured_minority_location_in_an_unconfigured_dominant_text() -> None:
    text, cut = _long_mixed_text()
    detector, _ = make_detector({}, config=_NER_ON)
    detector._analyzer.analyze = _locations_by_text()
    segments = [Segment(0, cut, _UNCONFIGURED), Segment(cut, len(text), _CONFIGURED)]

    result = detector.detect(text, _UNCONFIGURED, windowing=_WINDOWED, segments=segments)

    assert _location_starts(result) == {text.index("Ankara")}
