"""Pin the exact output of the date grammar over a branch-covering corpus."""

import hashlib
import json
from pathlib import Path

import pytest

from septum_core import date_grammar

_GOLDEN = json.loads(
    (Path(__file__).parent / "date_grammar_golden.json").read_text(encoding="utf-8")
)


def _digest(words) -> list:
    joined = "\n".join(sorted(words)).encode()
    return [len(words), hashlib.sha256(joined).hexdigest()[:16]]


@pytest.mark.parametrize("text", sorted(_GOLDEN["spans"]))
def test_find_date_spans_matches_golden(text):
    expected = [tuple(span) for span in _GOLDEN["spans"][text]]
    assert date_grammar.find_date_spans(text) == expected


def test_month_name_sets_are_unchanged():
    names = date_grammar._month_names()
    assert _digest(names.strong) == _GOLDEN["strong"]
    assert _digest(names.weak) == _GOLDEN["weak"]
    assert _digest(names.abbreviation_keys) == _GOLDEN["abbr"]


def test_grammar_fingerprint_is_unchanged():
    assert date_grammar.grammar_fingerprint() == _GOLDEN["fp"]
