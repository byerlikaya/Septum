"""Script-aware word boundaries: space-delimited text keeps its old results, unspaced text splits per glyph."""

from __future__ import annotations

import re

import pytest
from septum_core.text_boundaries import (
    is_script_break,
    is_span_boundary,
    is_unspaced_char,
    snap_between,
    snap_span,
    space_boundaries,
    word_bounded,
    word_count,
)

_LEGACY_BOUNDARY_CHARS = frozenset(" \t\n\r.,;:!?()[]{}\"'/-")


def _legacy_snap(text: str, start: int, end: int) -> tuple[int, int]:
    """The ASCII-only snap that detector.py used before this module existed."""
    while start > 0 and text[start - 1] not in _LEGACY_BOUNDARY_CHARS:
        start -= 1
    n = len(text)
    while end < n and text[end] not in _LEGACY_BOUNDARY_CHARS:
        end += 1
    return start, end


def _legacy_span_boundary(text: str, start: int, end: int) -> bool:
    """The sanitizer boundary check that existed before this module."""
    if start < 0 or end > len(text) or start >= end:
        return False
    if start > 0 and text[start - 1] not in _LEGACY_BOUNDARY_CHARS:
        return False
    if end < len(text) and text[end] not in _LEGACY_BOUNDARY_CHARS:
        return False
    return True


SPACED_SAMPLES = (
    "Ankara'da kara",
    "İstanbul'da Ali Çelik ile görüştü.",
    "ıİ ıi Iı Ii dotted DOTLESS",
    "Ahmet Yılmaz, Ayşe Demir; Ali - Veli",
    "Ali . Veli ( Ali ) [x] {y} \"z\" /w/ a-b",
    "mail: jane@example.com.",
    "Ali.Veli kara,kara ankara",
    "  leading and trailing  ",
    "",
    "Ankara",
    "Dr. Müller's Büro",
)


@pytest.mark.parametrize("text", SPACED_SAMPLES)
def test_word_count_equals_split_for_spaced_text(text: str) -> None:
    assert word_count(text) == len(text.split())


@pytest.mark.parametrize("text", SPACED_SAMPLES)
def test_snap_span_equals_legacy_for_spaced_text(text: str) -> None:
    n = len(text)
    for start in range(n):
        for end in range(start + 1, n + 1):
            assert snap_span(text, start, end) == _legacy_snap(text, start, end), (start, end)


@pytest.mark.parametrize("text", SPACED_SAMPLES)
def test_span_boundary_equals_legacy_for_spaced_text(text: str) -> None:
    n = len(text)
    for start in range(-1, n + 1):
        for end in range(start, n + 2):
            assert is_span_boundary(text, start, end) == _legacy_span_boundary(
                text, start, end
            ), (start, end)


SNAP_CASES = (
    ("Ankara'da yaşıyor", 0, 6, (0, 6)),
    ("张伟在北京工作", 0, 2, (0, 2)),
    ("张伟在北京工作", 3, 5, (3, 5)),
    ("北京市工作", 1, 2, (1, 2)),
    ("北京市工作", 0, 1, (0, 1)),
    ("Ankara市", 0, 5, (0, 6)),
    ("Ankar是", 0, 5, (0, 5)),
    ("Ali在北京", 3, 5, (3, 5)),
    ("北京Ali", 0, 2, (0, 2)),
    ("นิดหน่อย", 0, 1, (0, 2)),
    ("កាក", 0, 1, (0, 2)),
    ("สมชายใจดี", 1, 2, (1, 2)),
    ("한국 서울", 0, 1, (0, 2)),
    ("한국 서울", 4, 5, (3, 5)),
    ("北京2024", 0, 2, (0, 2)),
    ("北京2024", 3, 4, (2, 6)),
    ("张伟123在", 3, 4, (2, 5)),
    ("Ａｌｉ在", 0, 1, (0, 3)),
    ("Ali，Veli", 0, 3, (0, 8)),
)


@pytest.mark.parametrize("text, start, end, expected", SNAP_CASES)
def test_snap_span(text: str, start: int, end: int, expected: tuple[int, int]) -> None:
    assert snap_span(text, start, end) == expected


BOUNDARY_CASES = (
    ("Ankara'da yaşıyor", 0, 6, True),
    ("Ankara'da yaşıyor", 1, 6, False),
    ("请联系张伟在北京", 3, 5, True),
    ("请联系张伟在北京", 5, 7, True),
    ("kara市", 1, 4, False),
    ("นิด", 0, 1, False),
    ("สมชายใจดี", 0, 5, True),
    ("한국서울", 0, 2, False),
    ("Ali，Veli", 0, 3, False),
)


@pytest.mark.parametrize("text, start, end, expected", BOUNDARY_CASES)
def test_is_span_boundary(text: str, start: int, end: int, expected: bool) -> None:
    assert is_span_boundary(text, start, end) is expected


SCRIPT_BREAK_CASES = (
    ("ABC张", 3, True),
    ("张ABC", 1, True),
    ("ABCD", 2, False),
    ("Ali Veli", 3, False),
    ("张a", 0, False),
    ("张a", 1, True),
    ("张a", 2, False),
    ("が", 1, False),
    ("กข‌ค", 2, False),
    ("กข‍ค", 2, False),
    ("กข‌ค", 1, True),
    ("កា", 1, False),
)


@pytest.mark.parametrize("text, i, expected", SCRIPT_BREAK_CASES)
def test_is_script_break(text: str, i: int, expected: bool) -> None:
    assert is_script_break(text, i) is expected


WORD_COUNT_CASES = (
    ("北京", 2),
    ("张伟在北京工作", 7),
    ("北京 Ankara", 3),
    ("北京。", 2),
    ("Zhang张伟", 3),
    ("สมชาย", 5),
    ("นิด", 2),
    ("한국 서울", 2),
    ("北京2024", 3),
)


@pytest.mark.parametrize("text, expected", WORD_COUNT_CASES)
def test_word_count(text: str, expected: int) -> None:
    assert word_count(text) == expected


@pytest.mark.parametrize(
    "ch, expected",
    (("北", True), ("し", True), ("ก", True), ("ລ", True), ("한", False), ("a", False), ("ş", False), ("ı", False)),
)
def test_is_unspaced_char(ch: str, expected: bool) -> None:
    assert is_unspaced_char(ch) is expected


@pytest.mark.parametrize(
    "pattern, text, expected",
    (
        (r"[a-z]+", "请abc结束 xyz", ["abc", "xyz"]),
        (r"[a-z]+", "ab1c", []),
        (r"[a-z]+", "a-b", ["a", "b"]),
        (r"[a-z]+", "北京abc", ["abc"]),
        (r"[a-z]+", "abc北京", ["abc"]),
        (r"[a-z0-9]+", "请a1结束", ["a1"]),
        (r"[a-z]+", "请a1结束", []),
    ),
)
def test_word_bounded(pattern: str, text: str, expected: list[str]) -> None:
    assert re.findall(word_bounded(pattern), text) == expected


@pytest.mark.parametrize("letter", ("a", "é"))
def test_long_run_inside_unicode_text_matches_legacy(letter: str) -> None:
    text = "北京 " + letter * 500 + " ş"
    assert snap_span(text, 100, 101) == _legacy_snap(text, 100, 101) == (3, 503)


@pytest.mark.parametrize(
    "text", (*SPACED_SAMPLES, "นายสมชาย ใจดี, ทำงาน(ที่)กรุงเทพ", " กข ", "张伟在北京")
)
def test_snap_between_equals_the_whitespace_walk(text: str) -> None:
    boundaries = space_boundaries(text)
    n = len(text)
    for start in range(n + 1):
        for end in range(start, n + 1):
            assert snap_between(boundaries, n, start, end) == _legacy_snap(
                text, start, end
            ), (start, end)
