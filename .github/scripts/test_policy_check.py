"""Unit tests for the identifier rules in policy_check.py."""
from __future__ import annotations

import ast

import pytest
from policy_check import LANG_BRANCH_RE, bad_identifier, language_case_lines


@pytest.mark.parametrize(
    "name",
    [
        "TurkishPhoneRecognizer",
        "GermanIdValidator",
        "turkish_phone",
        "Turkish_phone",
        "validate_german_id",
        "IdFromIndia",
        "Spanish2Parser",
        "SpanishFlag",
    ],
)
def test_flags_country_or_language_words(name: str) -> None:
    assert bad_identifier(name)


@pytest.mark.parametrize(
    "name",
    [
        "UsageStats",
        "PolishedText",
        "GermaneRule",
        "IndianaX",
        "Chinaware",
        "usage_stats",
        "polished_text",
        "AUSTRALIA_PA",
        "__init__",
    ],
)
def test_ignores_words_that_only_contain_a_name(name: str) -> None:
    assert not bad_identifier(name)


@pytest.mark.parametrize(
    "line",
    [
        'if language == "tr":',
        'elif language == "tr":',
        'elif self.locale in ("de", "fr"):',
        'x = 1 if lang != "en-US" else 2',
    ],
)
def test_flags_language_branches(line: str) -> None:
    assert LANG_BRANCH_RE.search(line)


@pytest.mark.parametrize(
    "line",
    [
        'elif mode == "tr":',
        'elif language is None:',
        'if verify_language(language):',
        'notelif language == "tr"',
    ],
)
def test_ignores_other_branches(line: str) -> None:
    assert not LANG_BRANCH_RE.search(line)


def test_flags_match_case_on_language_code() -> None:
    tree = ast.parse(
        "match self.language:\n"
        "    case \"tr\":\n"
        "        pass\n"
        "    case \"de\" | \"fr\":\n"
        "        pass\n"
        "    case _:\n"
        "        pass\n"
    )
    assert language_case_lines(tree) == [2, 4]


def test_ignores_match_case_on_other_subjects() -> None:
    tree = ast.parse(
        "match mode:\n"
        "    case \"tr\":\n"
        "        pass\n"
        "match language:\n"
        "    case \"default\":\n"
        "        pass\n"
    )
    assert language_case_lines(tree) == []
