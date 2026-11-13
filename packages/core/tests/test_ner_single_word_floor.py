"""Single-word LOCATION spans clear a lower score floor than single-word ORGANIZATION_NAME."""

from __future__ import annotations

import pytest
from septum_core.detector import _NER_SINGLE_WORD_MIN_SCORE_PER_TYPE, Detector

_TEXT = "Ankara ve New York ofisleri"
_SINGLE = (0, 6)
_DOUBLE = (10, 18)


def _kept(label: str, bounds: tuple[int, int], score: float) -> list[tuple[int, int, str]]:
    raw = [{"entity_group": label, "start": bounds[0], "end": bounds[1], "score": score}]
    return [
        (span.start, span.end, span.entity_type)
        for span in Detector._from_ner_results(raw, _TEXT, "tr")
    ]


def test_location_floor_is_below_organization_floor() -> None:
    assert _NER_SINGLE_WORD_MIN_SCORE_PER_TYPE["LOCATION"] == 0.90
    assert _NER_SINGLE_WORD_MIN_SCORE_PER_TYPE["ORGANIZATION_NAME"] == 0.95


@pytest.mark.parametrize("score", [0.90, 0.92, 0.94, 0.97])
def test_single_word_location_at_or_above_the_floor_is_kept(score: float) -> None:
    assert _kept("LOC", _SINGLE, score) == [(0, 6, "LOCATION")]


@pytest.mark.parametrize("score", [0.60, 0.85, 0.89])
def test_single_word_location_below_the_floor_is_dropped(score: float) -> None:
    assert _kept("LOC", _SINGLE, score) == []


@pytest.mark.parametrize("score", [0.85, 0.90, 0.94])
def test_single_word_organization_below_its_floor_is_still_dropped(score: float) -> None:
    assert _kept("ORG", _SINGLE, score) == []


def test_single_word_organization_at_its_floor_is_kept() -> None:
    assert _kept("ORG", _SINGLE, 0.95) == [(0, 6, "ORGANIZATION_NAME")]


@pytest.mark.parametrize("label", ["LOC", "ORG"])
def test_multi_word_span_bypasses_the_single_word_floor(label: str) -> None:
    kept = _kept(label, _DOUBLE, 0.86)
    assert [(start, end) for start, end, _ in kept] == [_DOUBLE]
