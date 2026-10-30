"""Tests for the ``Label : Value`` pattern of :class:`TableFieldExtractor`.

The pattern was rewritten so a run of whitespace-only lines can no longer be
rescanned from every line start. These tests pin that the extracted
label/value pairs are unchanged for well-formed ``Label: Value`` lines (they
may differ only on one malformed shape: a whitespace-only line followed by a
line starting with ``:``, pinned explicitly below) and that the pathological
input completes in bounded time.
"""

from __future__ import annotations

import re
import time

import pytest

from septum_api.services.ingestion.table_extractor import TableFieldExtractor

_PREVIOUS = re.compile(r"^(.{2,80}?)\s*:\s*(.+?)$", re.MULTILINE | re.UNICODE)

_SAMPLES = [
    "Name : John Doe\nEmail: john@example.com",
    "  Indented Label:  value\n\tTabbed: other",
    "Label:\n  value on the next line",
    "Time: 10:30 meeting\nRatio : 3:1",
    "No colon here\nStill none",
    "Employee Number       :      12345\n\n\nStart Date: 2024-01-01",
    "a: b\n" + "x" * 90 + ": too long label\nEnd: ok",
    "Trailing:   \nNext: value",
]


def _pairs(pattern: re.Pattern[str], text: str) -> list[tuple[str, str, tuple[int, int]]]:
    pairs = []
    for match in pattern.finditer(text):
        label, value = match.group(1).strip(), match.group(2).strip()
        if label and value:
            pairs.append((label, value, match.span(2)))
    return pairs


@pytest.mark.parametrize("text", _SAMPLES)
def test_field_pattern_keeps_previous_pairs(text: str) -> None:
    assert _pairs(TableFieldExtractor._FIELD_PATTERN, text) == _pairs(_PREVIOUS, text)


def test_whitespace_only_line_before_leading_colon_line_now_yields_a_pair() -> None:
    text = "  \n:a:b"
    assert _pairs(_PREVIOUS, text) == []
    assert [(label, value) for label, value, _ in _pairs(TableFieldExtractor._FIELD_PATTERN, text)] == [
        (":a", "b")
    ]


_RELUCTANT_VALUE = re.compile(
    r"^(?=[^\S\n]*+\S)(.{2,80}?)\s*+:\s*+(.+?)$", re.MULTILINE | re.UNICODE
)

_VALUE_SAMPLES = _SAMPLES + [
    "Key: value with\r\ncarriage return",
    "Key: a: b: c\nOther:x",
    "Key: value after line separator\nNext: v",
    "Key: trailing spaces   \nLast: end",
    "Key: no newline at end",
    "Key: \n",
]


@pytest.mark.parametrize("text", _VALUE_SAMPLES)
def test_greedy_value_group_matches_reluctant_form(text: str) -> None:
    current = [m.span() + m.groups() for m in TableFieldExtractor._FIELD_PATTERN.finditer(text)]
    reluctant = [m.span() + m.groups() for m in _RELUCTANT_VALUE.finditer(text)]
    assert current == reluctant


def test_field_pattern_extracts_fields() -> None:
    fields = TableFieldExtractor()._extract_fields_from_text("Name : John Doe\nCity: Paris", 1)
    assert [(f.label, f.value) for f in fields] == [("Name", "John Doe"), ("City", "Paris")]


def test_whitespace_only_lines_complete_quickly() -> None:
    text = "   \n" * 50_000 + "x"
    started = time.perf_counter()
    list(TableFieldExtractor._FIELD_PATTERN.finditer(text))
    assert time.perf_counter() - started < 1.0
