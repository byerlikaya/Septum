"""Characterization of StructuredDocumentChunker section identification (pins behaviour before a refactor)."""

from __future__ import annotations

from septum_api.services.chunking_strategy import StructuredDocumentChunker


def _sections(lines: list[str]) -> list[dict]:
    return StructuredDocumentChunker()._identify_sections(lines)


def test_empty_input_yields_no_sections() -> None:
    assert _sections([]) == []


def test_text_before_the_first_heading_is_its_own_untitled_section() -> None:
    sections = _sections(["Intro text", "1. Scope", "body a", "2. Terms", "body b"])

    assert sections == [
        {"text": "Intro text", "title": None},
        {"text": "1. Scope\nbody a", "title": "1. Scope"},
        {"text": "2. Terms\nbody b", "title": "2. Terms"},
    ]


def test_blank_lines_before_a_section_are_dropped_and_inside_it_are_kept() -> None:
    sections = _sections(["", "   ", "1. A", "", "x"])

    assert sections == [{"text": "1. A\n\nx", "title": "1. A"}]


def test_colon_suffixed_line_starts_a_titled_section() -> None:
    sections = _sections(["before", "SUMMARY OF FINDINGS", "detail"])

    assert sections[-1] == {"text": "SUMMARY OF FINDINGS\ndetail", "title": "SUMMARY OF FINDINGS"}


def test_long_numbered_line_starts_a_section_without_a_title() -> None:
    long_line = "1. " + "WORD " * 60

    sections = _sections(["head", long_line.strip(), "tail"])

    assert sections[-1]["title"] is None
    assert sections[-1]["text"].startswith("1. WORD")


def test_title_is_truncated_to_one_hundred_characters() -> None:
    line = "1. " + "A" * 140

    sections = _sections([line])

    assert sections == [{"text": line, "title": line[:100]}]


def test_a_199_character_opener_keeps_a_title() -> None:
    line = "1. " + "A" * 196

    assert len(line) == 199
    assert _sections([line]) == [{"text": line, "title": line[:100]}]


def test_a_200_character_opener_has_no_title() -> None:
    line = "1. " + "A" * 197

    assert len(line) == 200
    assert _sections([line]) == [{"text": line, "title": None}]


def test_plain_lowercase_text_is_never_a_heading() -> None:
    sections = _sections(["plain sentence one", "plain sentence two"])

    assert sections == [{"text": "plain sentence one\nplain sentence two", "title": None}]
