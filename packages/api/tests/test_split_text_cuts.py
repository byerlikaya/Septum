"""Characterization tests for ``_split_text`` cut-point selection.

Pins which boundary the splitter prefers (blank line, sentence end, any
whitespace, hard cut) so the helper structure can change without altering
the produced pieces.
"""

from __future__ import annotations

import pytest

from septum_api.services.chunking_strategy import _split_text


def test_short_text_is_one_piece() -> None:
    assert _split_text("  hello world  ", 50) == ["hello world"]


def test_blank_text_yields_no_pieces() -> None:
    assert _split_text("   \n  ", 5) == []


def test_prefers_blank_line_over_sentence_end() -> None:
    text = "aaaa bbbb. cccc\n\ndddd eeee"
    assert _split_text(text, 20) == ["aaaa bbbb. cccc", "dddd eeee"]


def test_falls_back_to_sentence_end() -> None:
    text = "aaaa bbbb. cccc dddd eeee"
    assert _split_text(text, 16) == ["aaaa bbbb.", "cccc dddd eeee"]


def test_falls_back_to_whitespace() -> None:
    text = "aaaa bbbb cccc dddd"
    assert _split_text(text, 12) == ["aaaa bbbb", "cccc dddd"]


def test_hard_cut_without_boundary() -> None:
    assert _split_text("x" * 25, 10) == ["x" * 10, "x" * 10, "x" * 5]


def test_boundary_before_half_window_is_ignored() -> None:
    text = "ab " + "x" * 20
    assert _split_text(text, 10) == ["ab xxxxxxx", "x" * 10, "xxx"]


def test_last_boundary_in_window_wins() -> None:
    text = "a b c d e f g h i j k l"
    assert _split_text(text, 9) == ["a b c d", "e f g h", "i j k l"]


@pytest.mark.parametrize("max_size", [1, 2, 3, 7, 40])
def test_pieces_respect_max_size_and_cover_content(max_size: int) -> None:
    text = "One two. Three\n\nfour five six. seven eight nine ten eleven twelve."
    pieces = _split_text(text, max_size)
    assert all(len(p) <= max_size for p in pieces)
    assert "".join(pieces).replace(" ", "").replace("\n", "") == text.replace(" ", "").replace("\n", "")
