"""Lowercasing that never changes the text length.

``str.lower`` turns the dotted capital ``İ`` (U+0130) into two characters
(``i`` plus a combining dot above), so offsets found in lowered text drift
one position per ``İ`` when applied back to the original.
"""

from __future__ import annotations

import sys

from septum_core.text_utils import lower_preserving_offsets


def test_dotted_capital_lowers_to_plain_i() -> None:
    assert lower_preserving_offsets("İZMİR") == "izmir"


def test_matches_str_lower_elsewhere() -> None:
    text = "Ahmet KAYA, ÇAĞRI Işık, Straße ẞ, ΣΟΦΊΑ"
    assert lower_preserving_offsets(text) == text.lower()


def test_every_code_point_keeps_its_length() -> None:
    for code in range(sys.maxunicode + 1):
        assert len(lower_preserving_offsets(chr(code))) == 1, hex(code)
