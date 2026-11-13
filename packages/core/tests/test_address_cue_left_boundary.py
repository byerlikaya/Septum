"""A postal abbreviation cue needs a left boundary, not only a right one.

The tail of an ordinary word ("ST" in "first" or "test", "RD" in "toward")
is not an abbreviation, and two such tails plus a number must not mask a
line as a postal address. An abbreviation glued to a leading digit ("3Kat")
is not a cue either. A glyph of an unspaced script directly before the cue
still allows it, and the real address forms keep matching.
"""

from __future__ import annotations

import pytest
from septum_core.detector import StructuralAddressRecognizer


def _cues(text: str, abbreviations: frozenset[str] | None = None) -> list[str]:
    cue = StructuralAddressRecognizer(abbreviations)._structural_cue
    return [m.group() for m in cue.finditer(text)]


def _address_results(text: str, abbreviations: frozenset[str] | None = None) -> list[str]:
    results = StructuralAddressRecognizer(abbreviations).analyze(text, ["POSTAL_ADDRESS"])
    return [r.entity_type for r in results if r.entity_type == "POSTAL_ADDRESS"]


@pytest.mark.parametrize(
    "text",
    ["first", "test", "last 12 test 5", "Please test the first 3 items", "Note: first test 2024"],
)
def test_word_tail_is_not_an_address_cue(text: str) -> None:
    assert _cues(text) == []
    assert _address_results(text) == []


def test_custom_abbreviation_set_ignores_word_tails() -> None:
    abbreviations = frozenset({"RD", "LN"})
    text = "toward 12 and kiln 5"
    assert _cues(text, abbreviations) == []
    assert _address_results(text, abbreviations) == []


@pytest.mark.parametrize("text", ["3Kat", "12St"])
def test_abbreviation_glued_to_a_leading_digit_is_not_a_cue(text: str) -> None:
    assert _cues(text) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Main St. 12", ["St."]),
        ("Unit 4", ["Unit"]),
        ("NO: 5", ["NO"]),
        ("Apt. 3B", ["Apt."]),
    ],
)
def test_real_abbreviation_cues_still_match(text: str, expected: list[str]) -> None:
    assert [c for c in _cues(text) if not any(ch.isdigit() for ch in c)] == expected


def test_dense_line_is_still_detected() -> None:
    assert _address_results("X. 428 Y. Z NO: 10") != []


def test_multi_line_address_is_still_detected() -> None:
    text = "Main St. 12 NO: 5\nApt. 3B NO: 4"
    results = StructuralAddressRecognizer().analyze(text, ["POSTAL_ADDRESS"])
    assert [(r.start, r.end) for r in results] == [(0, 17), (18, 31)]


@pytest.mark.parametrize("text", ["住所APT号", "주소APT입니다", "ที่อยู่APTครับ", "العنوانAPTو"])
def test_glued_glyph_still_allows_the_cue(text: str) -> None:
    assert _cues(text) == ["APT"]


def test_abbreviation_prefix_of_a_word_is_not_a_cue() -> None:
    assert _cues("APTLY") == []
