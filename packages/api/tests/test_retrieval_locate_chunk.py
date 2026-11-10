"""Span mapping of chunks back onto their source text in the retrieval benchmark."""

from __future__ import annotations

from tests.benchmark_retrieval import locate_chunk, map_chunk_spans


def test_exact_match_returns_original_offsets() -> None:
    source = "alpha beta gamma delta"
    assert locate_chunk(source, "gamma", 0) == (11, 16)


def test_heading_joined_with_space_maps_across_line_break() -> None:
    source = "First clause can be done.\n\nSection 1\nThe next clause starts here and goes on."
    chunk = "First clause can be done. Section 1 The next clause starts here and goes on."
    assert locate_chunk(source, chunk, 0) == (0, len(source))


def test_normalised_span_points_at_original_characters() -> None:
    source = "prefix text.\n\n  Section 2 \t\n body of the section ends.\n\ntrailing"
    chunk = "Section 2 body of the section ends."
    span = locate_chunk(source, chunk, 0)
    assert span is not None
    start, end = span
    assert source[start:end] == "Section 2 \t\n body of the section ends."


def test_normalised_match_respects_cursor() -> None:
    source = "dup one\ntwo. filler. dup one\ntwo."
    first = locate_chunk(source, "dup one two.", 0)
    assert first == (0, 12)
    second = locate_chunk(source, "dup one two.", first[0] + 1)
    assert second == (source.rindex("dup"), len(source))


def test_unrelated_chunk_is_unmapped() -> None:
    assert locate_chunk("alpha beta gamma", "completely different text here", 0) is None


def test_map_chunk_spans_maps_merged_heading_chunk() -> None:
    source = "One sentence ends.\n\nArticle 1\nNext sentence follows."
    spans = map_chunk_spans(source, ["One sentence ends. Article 1 Next sentence follows."])
    assert spans == [(0, len(source))]
