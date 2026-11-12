"""The leak block's cut helper: spaced text keeps its whitespace cut, unspaced text snaps after punctuation."""

from __future__ import annotations

from tests.benchmark_retrieval import (
    LEAK_PUNCT_WINDOW,
    LEAK_SPACE_WINDOW,
    _snap_to_boundary,
)


def test_spaced_text_snaps_to_next_whitespace() -> None:
    text = "alpha beta gamma delta"
    assert _snap_to_boundary(text, 2) == 5


def test_whitespace_in_window_wins_over_earlier_punctuation_free_text() -> None:
    text = "ab" + "x" * 5 + " " + "y" * 30 + "。"
    assert _snap_to_boundary(text, 2) == 7


def test_whitespace_wins_over_punctuation_inside_the_narrow_window() -> None:
    text = "ab,cd ef"
    assert _snap_to_boundary(text, 0) == 5


def test_unspaced_text_snaps_after_punctuation() -> None:
    text = "电话费用说明，这是第二句话。"
    assert _snap_to_boundary(text, 1) == text.index("，") + 1


def test_punctuation_beyond_narrow_window_is_found_within_wide_window() -> None:
    text = "x" * 40 + "。" + "y" * 10
    assert _snap_to_boundary(text, 0) == 41


def test_punctuation_beyond_wide_window_is_ignored() -> None:
    text = "x" * (LEAK_PUNCT_WINDOW + 5) + "。"
    assert _snap_to_boundary(text, 0) == 0


def test_no_boundary_keeps_original_end() -> None:
    assert _snap_to_boundary("x" * 200, 10) == 10


def test_whitespace_just_past_narrow_window_is_used_when_no_punctuation() -> None:
    text = "x" * (LEAK_SPACE_WINDOW + 5) + " " + "y" * 10
    assert _snap_to_boundary(text, 0) == LEAK_SPACE_WINDOW + 5


def test_cut_at_text_end_is_unchanged() -> None:
    assert _snap_to_boundary("abc", 3) == 3
