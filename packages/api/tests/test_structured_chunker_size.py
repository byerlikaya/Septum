"""StructuredDocumentChunker output size: normal documents are pinned, oversized runs are bounded."""

from __future__ import annotations

import re
import time

import pytest

from septum_api.services.chunking_strategy import Chunk, StructuredDocumentChunker


class _FakeSplitter:
    """Stands in for the embedding-based splitter: returns the given pieces verbatim."""

    def __init__(self, pieces: list[str]) -> None:
        self._pieces = pieces

    def split_text(self, text: str) -> list[str]:
        return self._pieces


def _without_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(self: StructuredDocumentChunker) -> None:
        raise RuntimeError("embedding model unavailable")

    monkeypatch.setattr(StructuredDocumentChunker, "_get_semantic_splitter", _fail)


def _with_splitter(monkeypatch: pytest.MonkeyPatch, pieces: list[str]) -> None:
    monkeypatch.setattr(
        StructuredDocumentChunker, "_get_semantic_splitter", lambda self: _FakeSplitter(pieces)
    )


def _shape(chunks: list[Chunk]) -> list[tuple[int, str | None, str]]:
    return [(c.index, c.section_title, c.text) for c in chunks]


def _words(text: str) -> list[str]:
    return text.split()


# --- characterization: documents whose pieces already fit keep their exact output ---


def test_sections_under_the_limit_are_one_chunk_each(monkeypatch: pytest.MonkeyPatch) -> None:
    _without_model(monkeypatch)
    text = "Intro paragraph " * 10 + "\n1. Scope\nbody a\n2. Terms\nbody b"

    chunks = StructuredDocumentChunker(max_chunk_size=1200).chunk(text)

    assert _shape(chunks) == [
        (0, None, ("Intro paragraph " * 10).strip()),
        (1, "1. Scope", "1. Scope\nbody a"),
        (2, "2. Terms", "2. Terms\nbody b"),
    ]


def test_large_section_falls_back_to_paragraph_packing(monkeypatch: pytest.MonkeyPatch) -> None:
    _without_model(monkeypatch)
    para = ("word " * 50).strip()
    text = "1. Scope\n" + "\n\n".join([para] * 7)

    chunks = StructuredDocumentChunker(max_chunk_size=800).chunk(text)

    assert _shape(chunks) == [
        (0, "1. Scope", "1. Scope\n" + "\n\n".join([para] * 3)),
        (1, "1. Scope", "\n\n".join([para] * 3)),
        (2, "1. Scope", para),
    ]


def test_semantic_pieces_that_fit_are_kept_as_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    pieces = ["a" * 300, "tiny", "b" * 500]
    _with_splitter(monkeypatch, pieces)
    text = "1. Scope\n" + "x " * 700

    chunks = StructuredDocumentChunker(max_chunk_size=800).chunk(text)

    assert _shape(chunks) == [(0, "1. Scope", "a" * 300), (1, "1. Scope", "b" * 500)]


def test_unstructured_semantic_fallback_keeps_its_original_indices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pieces = ["a" * 300, "tiny", "b" * 500]
    _with_splitter(monkeypatch, pieces)

    chunks = StructuredDocumentChunker(max_chunk_size=800)._fallback_semantic_chunking("x " * 700)

    assert _shape(chunks) == [(0, None, "a" * 300), (2, None, "b" * 500)]


def test_text_shorter_than_min_chunk_size_is_returned_whole() -> None:
    assert _shape(StructuredDocumentChunker().chunk("short")) == [(0, None, "short")]


# --- regression: no chunk may exceed max_chunk_size ---


def test_single_huge_paragraph_is_split_at_sentence_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    _without_model(monkeypatch)
    text = "1. Scope\n" + "Lorem ipsum dolor sit amet. " * 200

    chunks = StructuredDocumentChunker(max_chunk_size=800).chunk(text)

    assert len(chunks) > 1
    assert all(len(c.text) <= 800 for c in chunks)
    assert all(c.text.endswith(".") for c in chunks)
    assert {c.section_title for c in chunks} == {"1. Scope"}
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert _words(" ".join(c.text for c in chunks)) == _words(text)


def test_oversized_semantic_piece_is_split(monkeypatch: pytest.MonkeyPatch) -> None:
    huge = "alpha beta gamma " * 400
    _with_splitter(monkeypatch, ["a" * 300, huge])
    text = "1. Scope\n" + "x " * 700

    chunks = StructuredDocumentChunker(max_chunk_size=800).chunk(text)

    assert chunks[0].text == "a" * 300
    assert all(len(c.text) <= 800 for c in chunks)
    assert _words(" ".join(c.text for c in chunks[1:])) == _words(huge)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_text_without_whitespace_is_hard_split(monkeypatch: pytest.MonkeyPatch) -> None:
    _without_model(monkeypatch)
    text = "1. Scope\n" + "z" * 2500

    chunks = StructuredDocumentChunker(max_chunk_size=800).chunk(text)

    assert all(len(c.text) <= 800 for c in chunks)
    assert "".join(c.text for c in chunks).replace("\n", "") == text.replace("\n", "")


def test_large_input_is_bounded_and_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    _without_model(monkeypatch)
    line_run = "\n".join("lorem ipsum dolor sit amet consectetur" for _ in range(30_000))
    one_paragraph = "Lorem ipsum dolor sit amet. " * 40_000

    for text in (line_run, one_paragraph):
        started = time.perf_counter()
        chunks = StructuredDocumentChunker(max_chunk_size=20_000).chunk(text)
        elapsed = time.perf_counter() - started

        assert len(text) > 1_000_000
        assert max(len(c.text) for c in chunks) <= 20_000
        assert re.sub(r"\s+", "", "".join(c.text for c in chunks)) == re.sub(r"\s+", "", text)
        assert elapsed < 3
