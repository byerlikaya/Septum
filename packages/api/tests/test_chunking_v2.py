"""Chunker v2: coverage, protected ranges, budgets, determinism and per-strategy behaviour."""

from __future__ import annotations

import random
import unicodedata
from bisect import bisect_right
from typing import Any, List, Sequence, Tuple

import docx
import pytest
from septum_core.anonymization_map import AnonymizationMap
from septum_core.span_projection import project, protected_ranges
from septum_core.spans import DetectedSpan, DetectionResult, ResolvedSpan

from septum_api.request_limits import (
    MAX_PRE_APPROVED_CHUNK_CHARS,
    MAX_SECTION_TITLE_CHARS,
    MIN_CHUNK_SIZE,
    max_chunk_overlap,
)
from septum_api.services.chunking import ChunkRecord, chunk_blocks
from septum_api.services.chunking.cutting import merge_ranges
from septum_api.services.ingestion.audio_ingester import _segment_blocks
from septum_api.services.ingestion.base import TextBlock, join_blocks
from septum_api.services.ingestion.docx_ingester import _document_blocks
from septum_api.services.ingestion.pdf_ingester import _page_blocks
from septum_api.services.ingestion.sheet_text import render_sheet

Range = Tuple[int, int]
SEEDS = range(60)


def _words(rng: random.Random, count: int) -> str:
    parts: List[str] = []
    for _ in range(count):
        word = "".join(rng.choice("abcdefghij") for _ in range(rng.randint(1, 9)))
        roll = rng.random()
        parts.append(word + (". " if roll < 0.1 else "\n" if roll < 0.15 else " "))
    return "".join(parts).rstrip()


def _blocks(rng: random.Random) -> List[TextBlock]:
    kinds = ["paragraph", "heading", "page", "part"]
    blocks = []
    for number in range(rng.randint(1, 12)):
        kind = rng.choice(kinds)
        text = _words(rng, rng.randint(1, 120) if kind != "heading" else rng.randint(1, 5))
        blocks.append(TextBlock(text=text, kind=kind, page=number + 1 if kind == "page" else None))
    return blocks


def _ranges(rng: random.Random, length: int) -> List[Range]:
    out = []
    for _ in range(rng.randint(0, 25)):
        a = rng.randrange(length)
        out.append((a, min(length, a + rng.randint(1, 60))))
    return out


def _strictly_inside(offset: int, ranges: Sequence[Range]) -> bool:
    return any(a < offset < b for a, b in ranges)


def _assert_covers(chunks: Sequence[ChunkRecord], text: str) -> None:
    assert chunks
    assert chunks[0].start == 0
    assert chunks[-1].end == len(text)
    for index, chunk in enumerate(chunks):
        assert chunk.index == index
        assert chunk.start < chunk.end
        assert chunk.text == text[chunk.start : chunk.end]
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt.start <= prev.end, "gap between chunks"
        assert nxt.start > prev.start
        assert nxt.end > prev.end


def _cases(strategy: str):
    for seed in SEEDS:
        rng = random.Random(seed)
        blocks = _blocks(rng)
        text = join_blocks(blocks)
        size = rng.choice([100, 150, 300, 800])
        overlap = rng.choice([0, 20, 50, 400])
        yield blocks, text, _ranges(rng, len(text)), size, overlap


@pytest.mark.parametrize("strategy", ["structured", "sliding"])
def test_full_coverage_without_gaps(strategy: str) -> None:
    for blocks, text, protected, size, overlap in _cases(strategy):
        chunks = chunk_blocks(blocks, strategy, protected=protected, chunk_size=size, overlap=overlap)
        _assert_covers(chunks, text)


@pytest.mark.parametrize("strategy", ["structured", "sliding"])
def test_no_cut_falls_inside_a_protected_range(strategy: str) -> None:
    for blocks, _, protected, size, overlap in _cases(strategy):
        chunks = chunk_blocks(blocks, strategy, protected=protected, chunk_size=size, overlap=overlap)
        for chunk in chunks:
            assert not _strictly_inside(chunk.start, protected)
            assert not _strictly_inside(chunk.end, protected)


@pytest.mark.parametrize("strategy", ["structured", "sliding"])
def test_chunks_fit_the_budget_unless_one_range_forces_growth(strategy: str) -> None:
    for blocks, text, protected, size, overlap in _cases(strategy):
        merged = merge_ranges(protected, len(text))
        chunks = chunk_blocks(blocks, strategy, protected=protected, chunk_size=size, overlap=overlap)
        for chunk in chunks:
            if chunk.end - chunk.start > size:
                assert (chunk.start, chunk.end) in merged


@pytest.mark.parametrize("strategy", ["structured", "sliding"])
def test_same_input_gives_same_chunks(strategy: str) -> None:
    for blocks, _, protected, size, overlap in _cases(strategy):
        first = chunk_blocks(blocks, strategy, protected=protected, chunk_size=size, overlap=overlap)
        second = chunk_blocks(blocks, strategy, protected=list(reversed(protected)), chunk_size=size, overlap=overlap)
        assert first == second


def test_overlap_never_exceeds_the_setting_and_steps_stay_long() -> None:
    for seed in SEEDS:
        rng = random.Random(seed)
        text = _words(rng, 600)
        size, overlap = 200, rng.choice([0, 50, 100, 500])
        window = min(overlap, max_chunk_overlap(size))
        chunks = chunk_blocks([TextBlock(text, "text")], "sliding", chunk_size=size, overlap=overlap)
        _assert_covers(chunks, text)
        for prev, nxt in zip(chunks, chunks[1:]):
            assert prev.end - nxt.start <= window
            assert nxt.start - prev.start >= (size - window) // 2


def test_overlap_start_is_snapped_outside_a_protected_range() -> None:
    text = "alpha " * 100
    protected = [(a, a + 12) for a in range(0, len(text) - 12, 36)]
    chunks = chunk_blocks([TextBlock(text, "text")], "sliding", protected=protected, chunk_size=200, overlap=100)

    _assert_covers(chunks, text)
    assert all(not _strictly_inside(c.start, protected) for c in chunks)


@pytest.mark.parametrize(
    "size,overlap",
    [(1, 0), (100, 99), (2_000, 1_999), (3 * MIN_CHUNK_SIZE, 3 * MIN_CHUNK_SIZE - 1)],
)
def test_stored_settings_cannot_make_the_window_crawl(size: int, overlap: int) -> None:
    text = "x" * 10_000
    chunks = chunk_blocks([TextBlock(text, "text")], "sliding", chunk_size=size, overlap=overlap)

    no_overlap = -(-len(text) // MIN_CHUNK_SIZE)
    assert len(chunks) <= 2 * no_overlap + 1
    assert all(len(c.text) <= max(size, MIN_CHUNK_SIZE) for c in chunks)


def test_oversized_chunk_size_is_capped_at_the_approval_limit() -> None:
    chunks = chunk_blocks([TextBlock("x" * 50_000, "text")], "sliding", chunk_size=10**9, overlap=0)

    assert max(len(c.text) for c in chunks) == MAX_PRE_APPROVED_CHUNK_CHARS


def test_valid_settings_are_used_unchanged() -> None:
    chunks = chunk_blocks([TextBlock("y" * 2000, "text")], "sliding", chunk_size=800, overlap=200)

    assert [len(c.text) for c in chunks] == [800, 800, 800]


def test_a_range_longer_than_the_budget_grows_the_chunk() -> None:
    text = "x" * 1000
    chunks = chunk_blocks([TextBlock(text, "text")], "sliding", protected=[(0, 400)], chunk_size=200, overlap=0)

    assert (chunks[0].start, chunks[0].end) == (0, 400)
    _assert_covers(chunks, text)


def test_a_range_longer_than_the_hard_cap_is_the_only_chunk_above_it() -> None:
    text = "x" * 60_000
    span = (100, 100 + MAX_PRE_APPROVED_CHUNK_CHARS + 500)
    chunks = chunk_blocks([TextBlock(text, "text")], "sliding", protected=[span], chunk_size=800, overlap=0)

    oversize = [c for c in chunks if len(c.text) > MAX_PRE_APPROVED_CHUNK_CHARS]
    assert [(c.start, c.end) for c in oversize] == [span]


def test_cut_prefers_block_boundary_then_sentence_then_whitespace() -> None:
    blocks = [TextBlock("a" * 60, "paragraph"), TextBlock("b" * 60, "paragraph")]
    chunks = chunk_blocks(blocks, "structured", chunk_size=100, overlap=0)
    assert [(c.start, c.end) for c in chunks] == [(0, 61), (61, 121)]

    sentence = "one two three four five six seven eight nine ten eleven twelve thirteen. fourteen fifteen sixteen seventeen eighteen."
    chunks = chunk_blocks([TextBlock(sentence, "text")], "sliding", chunk_size=100, overlap=0)
    assert chunks[0].text.endswith("thirteen. ")

    words = " ".join(["word"] * 60)
    chunks = chunk_blocks([TextBlock(words, "text")], "sliding", chunk_size=100, overlap=0)
    assert all(c.text.endswith(" ") for c in chunks[:-1])


def test_hard_cut_is_the_last_resort() -> None:
    chunks = chunk_blocks([TextBlock("z" * 250, "text")], "sliding", chunk_size=100, overlap=0)

    assert [len(c.text) for c in chunks] == [100, 100, 50]


def test_length_function_caps_the_chunk() -> None:
    rng = random.Random(7)
    text = _words(rng, 300)
    chunks = chunk_blocks(
        [TextBlock(text, "text")],
        "sliding",
        chunk_size=1_000,
        overlap=0,
        length_fn=lambda s: len(s.split()),
        max_length=20,
    )

    _assert_covers(chunks, text)
    assert all(len(c.text.split()) <= 20 for c in chunks)


def test_empty_input_gives_no_chunks() -> None:
    assert chunk_blocks([], "structured") == []
    assert chunk_blocks([TextBlock("", "text")], "sliding") == []


def test_invalid_range_is_rejected_even_for_empty_text() -> None:
    with pytest.raises(ValueError):
        chunk_blocks([TextBlock("", "text")], "sliding", protected=[(0, 5)])
    with pytest.raises(ValueError):
        chunk_blocks([], "structured", protected=[(-1, 0)])


def test_long_title_is_never_cut_inside_a_protected_range() -> None:
    limit = MAX_SECTION_TITLE_CHARS
    heading = "w" * (limit - 12) + "a@example.com" + "x" * 50
    blocks = [TextBlock(heading, "heading"), TextBlock("body " * 200, "paragraph")]
    rng = (limit - 12, limit - 12 + len("a@example.com"))
    chunks = chunk_blocks(blocks, "structured", protected=[rng], chunk_size=300, overlap=0)

    title = chunks[-1].section_title
    assert title is not None and len(title) == rng[0]
    assert (chunks[-1].section_title_start, chunks[-1].section_title_end) == (0, len(heading))

    whole = chunk_blocks(blocks, "structured", protected=[(0, limit + 5)], chunk_size=300, overlap=0)
    assert whole[-1].section_title == ""


def test_unknown_strategy_is_rejected() -> None:
    invalid: Any = "nope"
    with pytest.raises(ValueError):
        chunk_blocks([TextBlock("a", "text")], invalid)


def test_pdf_pages_carry_their_page_number() -> None:
    pages = [TextBlock(_words(random.Random(n), 150), "page", page=n) for n in range(1, 5)]
    text = join_blocks(pages)
    starts = [0]
    for page in pages[:-1]:
        starts.append(starts[-1] + len(page.text) + 2)
    chunks = chunk_blocks(pages, "structured", chunk_size=300, overlap=50)

    for chunk in chunks:
        owner = max(i for i, s in enumerate(starts) if s <= chunk.start)
        assert chunk.page == owner + 1
    for start, number in zip(starts, range(1, 5)):
        assert any(c.start == start and c.page == number for c in chunks)
    _assert_covers(chunks, text)


def test_structured_section_title_is_the_heading_in_effect() -> None:
    blocks = [
        TextBlock("Intro", "heading"),
        TextBlock("first body " * 20, "paragraph"),
        TextBlock("Methods", "heading"),
        TextBlock("second body " * 20, "paragraph"),
    ]
    chunks = chunk_blocks(blocks, "structured", chunk_size=300, overlap=0)

    assert chunks[0].section_title == "Intro"
    assert chunks[-1].section_title == "Methods"


def _sheet(title: str, rows: int, columns: int) -> List[TextBlock]:
    grid = [["h"] * columns] + [[f"r{r}c{c}" for c in range(columns)] for r in range(rows)]
    blocks, _, _ = render_sheet(title, grid)
    return blocks


def test_table_chunks_hold_the_cell_budget_and_repeat_the_header_as_title() -> None:
    blocks = _sheet("Alpha", rows=40, columns=10) + _sheet("Beta", rows=12, columns=10)
    text = join_blocks(blocks)
    chunks = chunk_blocks(blocks, "table", spreadsheet_chunk_size=50)

    _assert_covers(chunks, text)
    assert all(a.end == b.start for a, b in zip(chunks, chunks[1:]))
    for chunk in chunks:
        lines = [ln for ln in chunk.text.split("\n") if ln and not ln.startswith("# Sheet:")]
        assert len(lines) <= 5
        assert chunk.section_title is not None
        assert chunk.section_title.startswith(f"# Sheet: {chunk.sheet}")
    assert {c.sheet for c in chunks} == {"Alpha", "Beta"}
    assert chunks[0].text.startswith("# Sheet: Alpha")
    assert all(text[c.start - 1] == "\n" for c in chunks[1:])


def test_table_chunk_never_splits_a_row_unless_the_row_is_oversize() -> None:
    blocks = _sheet("S", rows=30, columns=4)
    text = join_blocks(blocks)
    chunks = chunk_blocks(blocks, "table", spreadsheet_chunk_size=8)

    assert all(c.start == 0 or text[c.start - 1] == "\n" for c in chunks)


def _segments(count: int, seconds: float) -> List[TextBlock]:
    return [
        TextBlock(f" segment number {n} says something. ", "segment", t_start=n * seconds, t_end=(n + 1) * seconds)
        for n in range(count)
    ]


def test_audio_cuts_only_on_segment_boundaries_and_carries_times() -> None:
    blocks = _segments(10, 10.0)
    text = join_blocks(blocks)
    boundaries = {0}
    for block in blocks:
        boundaries.add(max(boundaries) + len(block.text))
    chunks = chunk_blocks(blocks, "audio", audio_chunk_size=30)

    _assert_covers(chunks, text)
    assert all(c.start in boundaries and c.end in boundaries for c in chunks)
    assert [(c.t_start, c.t_end) for c in chunks] == [(0.0, 30.0), (30.0, 60.0), (60.0, 90.0), (90.0, 100.0)]


def test_audio_segment_longer_than_the_window_stays_whole() -> None:
    tail = [TextBlock(b.text, "segment", t_start=b.t_start + 120.0, t_end=b.t_end + 120.0) for b in _segments(2, 10.0)]
    blocks = [TextBlock(" long talk. ", "segment", t_start=0.0, t_end=120.0)] + tail
    chunks = chunk_blocks(blocks, "audio", audio_chunk_size=30)

    assert (chunks[0].t_start, chunks[0].t_end) == (0.0, 120.0)


def test_audio_respects_protected_ranges_across_a_segment_boundary() -> None:
    blocks = _segments(6, 10.0)
    first = len(blocks[0].text)
    protected = [(first - 3, first + 3)]
    chunks = chunk_blocks(blocks, "audio", audio_chunk_size=10, protected=protected)

    assert all(not _strictly_inside(c.start, protected) for c in chunks)
    assert all(not _strictly_inside(c.end, protected) for c in chunks)


_SCRIPT_TEXTS = {
    "cjk_punctuated": "这是一个用于测试的句子。它没有任何空格！你觉得怎么样？" * 40,
    "cjk_bare": "这是一个用于测试的文本它没有任何标点也没有空格" * 40,
    "japanese": "これはテストです。空白はありません！本当ですか？" * 40,
    "thai": "สวัสดีครับผมชื่อสมชายเป็นคนไทยอาศัยอยู่ในกรุงเทพมหานคร" * 30,
    "arabic": "مرحبا بالعالم؟ هذا اختبار للنص العربي. نحن نختبر القطع۔ " * 30,
    "devanagari": "यह एक परीक्षण वाक्य है। हम पाठ को सही जगह काटते हैं। " * 40,
}


def _combining(char: str) -> bool:
    return unicodedata.category(char).startswith("M")


@pytest.mark.parametrize("name", sorted(_SCRIPT_TEXTS))
@pytest.mark.parametrize("strategy", ["structured", "sliding"])
def test_non_latin_scripts_keep_the_invariants(name: str, strategy: str) -> None:
    text = _SCRIPT_TEXTS[name]
    for seed in range(12):
        rng = random.Random(seed)
        protected = _ranges(rng, len(text))
        size = rng.choice([100, 150, 300])
        overlap = rng.choice([0, 20, 50])
        chunks = chunk_blocks([TextBlock(text, "text")], strategy, protected=protected, chunk_size=size, overlap=overlap)
        merged = merge_ranges(protected, len(text))

        _assert_covers(chunks, text)
        for chunk in chunks:
            assert not _strictly_inside(chunk.start, protected)
            assert not _strictly_inside(chunk.end, protected)
            if chunk.end - chunk.start > size:
                assert (chunk.start, chunk.end) in merged
        assert chunks == chunk_blocks(
            [TextBlock(text, "text")], strategy, protected=list(reversed(protected)), chunk_size=size, overlap=overlap
        )


@pytest.mark.parametrize("name", ["cjk_bare", "japanese", "thai"])
def test_unspaced_scripts_never_cut_before_a_combining_mark(name: str) -> None:
    text = _SCRIPT_TEXTS[name]
    for size in (100, 117, 133, 250):
        chunks = chunk_blocks([TextBlock(text, "text")], "sliding", chunk_size=size, overlap=0)
        assert all(not _combining(text[c.end]) for c in chunks[:-1])
        assert all(not _combining(c.text[0]) for c in chunks)


@pytest.mark.parametrize("name", ["cjk_punctuated", "japanese"])
def test_unspaced_sentence_marks_are_preferred_cuts(name: str) -> None:
    text = _SCRIPT_TEXTS[name]
    chunks = chunk_blocks([TextBlock(text, "text")], "sliding", chunk_size=100, overlap=0)

    assert all(c.text[-1] in "。！？" for c in chunks[:-1])


@pytest.mark.parametrize("name", ["arabic", "devanagari"])
def test_spaced_non_latin_sentences_end_chunks(name: str) -> None:
    text = _SCRIPT_TEXTS[name]
    chunks = chunk_blocks([TextBlock(text, "text")], "sliding", chunk_size=120, overlap=0)

    assert all(c.text.rstrip()[-1] in ".؟۔।" for c in chunks[:-1])


def test_thai_without_boundaries_is_cut_between_glyphs_not_in_one_stroke() -> None:
    text = _SCRIPT_TEXTS["thai"]
    chunks = chunk_blocks([TextBlock(text, "text")], "sliding", chunk_size=100, overlap=0)

    assert len(chunks) > 1
    assert all(len(c.text) <= 100 for c in chunks)
    assert chunks[0].end > 50


def test_mixed_script_blocks_with_pages_and_protected_ranges() -> None:
    pages = [
        TextBlock(_SCRIPT_TEXTS["cjk_punctuated"][:300], "page", page=1),
        TextBlock(_SCRIPT_TEXTS["thai"][:300], "page", page=2),
        TextBlock(_SCRIPT_TEXTS["arabic"][:300], "page", page=3),
    ]
    text = join_blocks(pages)
    protected = [(a, a + 7) for a in range(5, len(text) - 7, 47)]
    chunks = chunk_blocks(pages, "structured", protected=protected, chunk_size=150, overlap=30)

    _assert_covers(chunks, text)
    assert all(not _strictly_inside(c.start, protected) and not _strictly_inside(c.end, protected) for c in chunks)
    assert [c.page for c in chunks if c.start == 0] == [1]
    assert {c.page for c in chunks} == {1, 2, 3}


def _span(start: int, end: int, placeholder: str, entity_type: str = "EMAIL_ADDRESS") -> ResolvedSpan:
    return ResolvedSpan(start, end, entity_type, placeholder, 1.0, None)


def _projection_fixture():
    """A document with detected emails and a blocklisted name mentioned again undetected."""
    anon_map = AnonymizationMap(document_id=1, language="en")
    parts: List[str] = []
    spans: List[ResolvedSpan] = []
    name = "Johnson Smithers"
    name_placeholder = anon_map.add_entity(name, "PERSON_NAME")
    position = 0
    for n in range(40):
        filler = f"lorem ipsum dolor amet {n} cras vivamus nunc. "
        parts.append(filler)
        position += len(filler)
        if n % 3 == 0:
            email = f"user{n}.test@example.com"
            placeholder = anon_map.add_entity(email, "EMAIL_ADDRESS")
            spans.append(_span(position, position + len(email), placeholder))
            parts.append(email + " ")
            position += len(email) + 1
        if n % 7 == 0:
            spans.append(_span(position, position + len(name), name_placeholder, "PERSON_NAME"))
            parts.append(name + " ")
            position += len(name) + 1
        if n % 5 == 0:
            parts.append("later Smithers left ")
            position += len("later Smithers left ")
    text = "".join(parts)
    detection = DetectionResult(
        text=text,
        language="en",
        spans=tuple(DetectedSpan(s.start, s.end, s.entity_type, s.score, s.core) for s in spans),
        semantic_enabled=False,
    )
    return text, detection, spans, anon_map


@pytest.mark.parametrize("strategy", ["structured", "sliding"])
@pytest.mark.parametrize("size,overlap", [(100, 0), (150, 40), (300, 100)])
def test_real_protected_ranges_are_respected_and_never_clip_a_span(strategy: str, size: int, overlap: int) -> None:
    text, detection, spans, anon_map = _projection_fixture()
    protected = protected_ranges(detection, spans, anon_map)
    assert len(protected) >= len(spans) // 2

    chunks = chunk_blocks([TextBlock(text, "text")], strategy, protected=protected, chunk_size=size, overlap=overlap)
    _assert_covers(chunks, text)
    for chunk in chunks:
        assert not _strictly_inside(chunk.start, protected)
        assert not _strictly_inside(chunk.end, protected)

    projected = project(detection, spans, [(c.start, c.end) for c in chunks], anon_map)
    assert len(projected) == len(chunks)
    for chunk in projected:
        assert not any(s.clipped for s in chunk.spans)
        assert "example.com" not in chunk.masked_text
        assert "Smithers" not in chunk.masked_text


def test_real_protected_ranges_hold_across_a_block_boundary() -> None:
    text, detection, spans, anon_map = _projection_fixture()
    protected = protected_ranges(detection, spans, anon_map)
    blocks = [TextBlock(text[: len(text) // 2], "paragraph"), TextBlock(text[len(text) // 2 :], "paragraph")]
    joined = join_blocks(blocks)
    shifted = [(a, b) for a, b in protected if b <= len(blocks[0].text)] + [
        (a + 1, b + 1) for a, b in protected if a >= len(blocks[0].text)
    ]
    chunks = chunk_blocks(blocks, "structured", protected=shifted, chunk_size=200, overlap=50)

    _assert_covers(chunks, joined)
    assert all(not _strictly_inside(c.start, shifted) and not _strictly_inside(c.end, shifted) for c in chunks)


def _ingester_blocks() -> List[TextBlock]:
    """Blocks as the PDF, DOCX and audio ingesters build them, without files or models."""
    pdf = _page_blocks([_words(random.Random(n), 90) for n in range(1, 4)])
    document = docx.Document()
    document.add_heading("Overview", 1)
    document.add_paragraph(_words(random.Random(11), 80))
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "cell text"
    document.add_heading("Details", 1)
    document.add_paragraph(_words(random.Random(12), 80))
    return pdf + _document_blocks(document)


def test_docx_and_pdf_shaped_blocks_chunk_with_full_coverage_and_titles() -> None:
    blocks = _ingester_blocks()
    text = join_blocks(blocks)
    assert {b.kind for b in blocks} >= {"page", "heading", "paragraph", "table"}

    for size, overlap in [(100, 0), (200, 50), (800, 200)]:
        chunks = chunk_blocks(blocks, "structured", chunk_size=size, overlap=overlap)
        _assert_covers(chunks, text)
        details = text.index("Details")
        for chunk in chunks:
            if chunk.start >= details:
                assert chunk.section_title == "Details"
            elif chunk.start >= text.index("Overview"):
                assert chunk.section_title == "Overview"
            else:
                assert chunk.section_title is None
        assert all(c.page is None for c in chunks if c.start >= text.index("Overview"))
        assert all(c.page in (1, 2, 3) for c in chunks if c.start < text.index("Overview"))


def test_pdf_fields_block_has_no_page_and_does_not_shift_page_numbers() -> None:
    pages = _page_blocks([_words(random.Random(n), 60) for n in range(1, 3)])
    blocks = [TextBlock("Name : value\nCity : other", "fields")] + pages
    text = join_blocks(blocks)
    chunks = chunk_blocks(blocks, "structured", chunk_size=100, overlap=0)

    _assert_covers(chunks, text)
    assert chunks[0].page is None
    assert {c.page for c in chunks} == {None, 1, 2}
    first_page = len(blocks[0].text) + 2
    assert next(c for c in chunks if c.start == first_page).page == 1


def test_whisper_segment_blocks_from_the_ingester_carry_their_times() -> None:
    segments = [
        {"text": f" sentence number {n} is spoken here.", "start": n * 7.0, "end": n * 7.0 + 6.5}
        for n in range(20)
    ]
    text = "".join(s["text"] for s in segments)
    blocks = _segment_blocks(text, segments)
    assert join_blocks(blocks) == text

    chunks = chunk_blocks(blocks, "audio", audio_chunk_size=30)
    _assert_covers(chunks, text)
    assert all(a.end == b.start for a, b in zip(chunks, chunks[1:]))
    for chunk in chunks:
        inside = [s for s in segments if chunk.start <= text.index(s["text"]) < chunk.end]
        assert chunk.t_start == inside[0]["start"]
        assert chunk.t_end == inside[-1]["end"]
        assert chunk.t_end - chunk.t_start <= 30


def test_whisper_text_without_locatable_segments_is_one_block_chunked_by_size() -> None:
    segments = [{"text": "not in the text", "start": 0.0, "end": 4.0}, {"text": "neither", "start": 4.0, "end": 9.0}]
    text = _words(random.Random(3), 400)
    blocks = _segment_blocks(text, segments)
    assert len(blocks) == 1

    chunks = chunk_blocks(blocks, "audio", audio_chunk_size=30, chunk_size=200)
    _assert_covers(chunks, text)
    assert all((c.t_start, c.t_end) == (0.0, 9.0) for c in chunks)


def test_spreadsheet_ingester_blocks_chunk_by_cells_per_sheet() -> None:
    rows = [["a", "b", "c", "d"]] + [[f"v{r}", f"w{r}", f"x{r}", f"y{r}"] for r in range(50)]
    blocks, row_count, labels = render_sheet("Data", rows)
    assert row_count == 51 and labels is not None
    text = join_blocks(blocks)
    chunks = chunk_blocks(blocks, "table", spreadsheet_chunk_size=20)

    _assert_covers(chunks, text)
    body = [c for c in chunks if not c.text.startswith("# Sheet:")]
    assert all(c.text.count("\n") + (0 if c.text.endswith("\n") else 1) == 5 for c in body[:-1])
    assert all(c.sheet == "Data" for c in chunks)
    assert all(c.section_title == blocks[0].text for c in chunks)
    assert chunks[0].text.startswith(blocks[0].text)


def test_table_row_groups_hold_exactly_the_cell_budget() -> None:
    blocks = _sheet("S", rows=40, columns=10)
    text = join_blocks(blocks)
    chunks = chunk_blocks(blocks, "table", spreadsheet_chunk_size=50)

    _assert_covers(chunks, text)
    assert chunks[0].text.startswith(blocks[0].text)
    body_lines = [len([ln for ln in c.text.split("\n") if ln and not ln.startswith("# Sheet:")]) for c in chunks]
    assert body_lines[:-1] == [5] * (len(chunks) - 1)
    assert sum(body_lines) == 41


def test_table_chunks_never_straddle_two_sheets() -> None:
    blocks = _sheet("One", rows=7, columns=4) + _sheet("Two", rows=7, columns=4)
    text = join_blocks(blocks)
    second = text.index("# Sheet: Two")
    chunks = chunk_blocks(blocks, "table", spreadsheet_chunk_size=1000)

    _assert_covers(chunks, text)
    assert [c.sheet for c in chunks] == ["One", "Two"]
    assert chunks[1].start == second


def test_table_cut_inside_a_protected_cell_moves_to_a_valid_row_edge() -> None:
    blocks = _sheet("S", rows=20, columns=4)
    text = join_blocks(blocks)
    row_edges = [i + 1 for i, ch in enumerate(text) if ch == "\n"]
    target = row_edges[6]
    protected = [(target - 4, target + 4)]
    chunks = chunk_blocks(blocks, "table", spreadsheet_chunk_size=8, protected=protected)

    _assert_covers(chunks, text)
    assert all(not _strictly_inside(c.start, protected) and not _strictly_inside(c.end, protected) for c in chunks)


def test_table_oversize_row_is_split_by_the_length_ceiling() -> None:
    blocks = [TextBlock("# Sheet: W", "sheet_header", sheet="W"), TextBlock("a\tb\n" + "q" * 500, "rows", sheet="W", row_offsets=[0, 4])]
    text = join_blocks(blocks)
    chunks = chunk_blocks(blocks, "table", spreadsheet_chunk_size=2, max_length=120)

    _assert_covers(chunks, text)
    assert all(len(c.text) <= 120 for c in chunks)
    assert len(chunks) > 4


def test_structured_packs_small_blocks_together_up_to_the_budget() -> None:
    blocks = [TextBlock(f"block{n:02d} " + "w" * 20, "paragraph") for n in range(10)]
    text = join_blocks(blocks)
    starts = [0]
    for block in blocks[:-1]:
        starts.append(starts[-1] + len(block.text) + 1)
    chunks = chunk_blocks(blocks, "structured", chunk_size=100, overlap=40)

    _assert_covers(chunks, text)
    assert all(c.start in starts for c in chunks)
    assert all(c.end in starts or c.end == len(text) for c in chunks)
    assert all(c.end - c.start <= 100 for c in chunks)
    assert len(chunks) == 4
    assert all(a.end == b.start for a, b in zip(chunks, chunks[1:]))


def test_structured_overlaps_only_inside_a_split_block() -> None:
    blocks = [TextBlock("short lead", "paragraph"), TextBlock(_words(random.Random(5), 200), "paragraph"), TextBlock("short tail", "paragraph")]
    text = join_blocks(blocks)
    big_start, big_end = len(blocks[0].text) + 1, len(blocks[0].text) + 1 + len(blocks[1].text)
    chunks = chunk_blocks(blocks, "structured", chunk_size=200, overlap=60)

    _assert_covers(chunks, text)
    inside = [(a, b) for a, b in zip(chunks, chunks[1:]) if big_start < b.start < big_end]
    assert inside
    assert all(0 < a.end - b.start <= 60 for a, b in inside)
    assert any(c.start == big_start for c in chunks)
    assert any(c.end == big_end + 1 or c.end == len(text) for c in chunks)


def test_sliding_overlap_is_present_and_the_shared_text_is_identical() -> None:
    text = " ".join(f"word{n}" for n in range(400))
    chunks = chunk_blocks([TextBlock(text, "text")], "sliding", chunk_size=200, overlap=60)

    assert len(chunks) > 5
    for prev, nxt in zip(chunks, chunks[1:]):
        shared = prev.end - nxt.start
        assert 0 < shared <= 60
        assert prev.text[-shared:] == nxt.text[:shared]
        assert text[nxt.start - 1] == " "


def test_zero_overlap_leaves_chunks_adjacent() -> None:
    text = " ".join(f"word{n}" for n in range(400))
    chunks = chunk_blocks([TextBlock(text, "text")], "sliding", chunk_size=200, overlap=0)

    assert all(a.end == b.start for a, b in zip(chunks, chunks[1:]))


def test_chunk_text_equals_joined_blocks_slice_for_every_strategy() -> None:
    blocks = _sheet("S", rows=15, columns=3)
    segs = _segments(8, 10.0)
    paragraphs = _blocks(random.Random(2))
    for strategy, source in [("table", blocks), ("audio", segs), ("structured", paragraphs), ("sliding", paragraphs)]:
        joined = join_blocks(source)
        for chunk in chunk_blocks(source, strategy):
            assert chunk.text == joined[chunk.start : chunk.end]
            assert chunk.strategy == strategy


def test_sliding_and_audio_never_carry_a_section_title() -> None:
    blocks = [TextBlock("Heading", "heading"), TextBlock(_words(random.Random(1), 300), "paragraph")]
    assert all(c.section_title is None for c in chunk_blocks(blocks, "sliding", chunk_size=100, overlap=0))
    assert all(c.section_title is None for c in chunk_blocks(_segments(5, 10.0), "audio", audio_chunk_size=10))


def test_section_title_is_truncated_to_the_limit() -> None:
    title = "T" * (MAX_SECTION_TITLE_CHARS + 100)
    chunks = chunk_blocks([TextBlock(title, "heading"), TextBlock("body " * 50, "paragraph")], "structured", chunk_size=100, overlap=0)

    assert all(c.section_title == title[:MAX_SECTION_TITLE_CHARS] for c in chunks)


def test_every_chunk_of_a_long_section_repeats_its_heading() -> None:
    blocks = [TextBlock("Intro", "heading"), TextBlock(_words(random.Random(9), 300), "paragraph")]
    chunks = chunk_blocks(blocks, "structured", chunk_size=100, overlap=20)

    assert len(chunks) > 3
    assert all(c.section_title == "Intro" for c in chunks)


def test_chunk_metadata_comes_from_the_block_holding_start_and_last_character() -> None:
    blocks = [
        TextBlock("a " * 20, "page", page=1),
        TextBlock("b " * 20, "page", page=2),
        TextBlock("c " * 20, "page", page=3),
    ]
    text = join_blocks(blocks)
    chunks = chunk_blocks(blocks, "sliding", chunk_size=100, overlap=0)

    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk.page == 1 + (chunk.start > len(blocks[0].text) + 1) + (chunk.start >= len(blocks[0].text) + len(blocks[1].text) + 4)
    assert chunks[0].start == 0 and chunks[-1].end == len(text)


def test_protected_range_at_the_very_end_and_start_is_handled() -> None:
    text = "alpha beta gamma delta " * 30
    chunks = chunk_blocks(
        [TextBlock(text, "text")],
        "sliding",
        protected=[(0, 4), (len(text) - 4, len(text))],
        chunk_size=100,
        overlap=20,
    )

    _assert_covers(chunks, text)


@pytest.mark.parametrize("bad", [(-1, 5), (3, 2), (0, 10**6), (0.5, 3), (True, 3)])
def test_malformed_protected_ranges_raise_without_document_text(bad) -> None:
    text = "secret-token " * 50
    with pytest.raises((ValueError, TypeError)) as caught:
        chunk_blocks([TextBlock(text, "text")], "sliding", protected=[bad])
    assert "secret" not in str(caught.value)


def test_zero_length_protected_range_is_ignored() -> None:
    text = "word " * 400
    assert chunk_blocks([TextBlock(text, "text")], "sliding", protected=[(7, 7)]) == chunk_blocks(
        [TextBlock(text, "text")], "sliding"
    )


def test_section_title_offsets_point_at_the_raw_title() -> None:
    blocks = [
        TextBlock("Intro", "heading"),
        TextBlock("body " * 20, "paragraph"),
        TextBlock("x" * (MAX_SECTION_TITLE_CHARS + 50), "heading"),
        TextBlock("tail " * 20, "paragraph"),
    ]
    text = join_blocks(blocks)
    chunks = chunk_blocks(blocks, "structured", chunk_size=MIN_CHUNK_SIZE)
    assert chunks[0].section_title == "Intro"
    for chunk in chunks:
        if chunk.section_title is None:
            assert chunk.section_title_start is None and chunk.section_title_end is None
            continue
        source = text[chunk.section_title_start : chunk.section_title_end]
        assert source.startswith(chunk.section_title)
    long = [c for c in chunks if c.section_title and len(c.section_title) == MAX_SECTION_TITLE_CHARS]
    assert long and long[0].section_title_end - long[0].section_title_start == MAX_SECTION_TITLE_CHARS + 50


@pytest.mark.parametrize("strategy", ["structured", "sliding"])
def test_token_cap_probes_stay_near_the_chunk_size(strategy: str) -> None:
    rng = random.Random(3)
    paras = [_words(rng, rng.randint(20, 120)) + "." for _ in range(400)]
    blocks = (
        [TextBlock(p, "paragraph") for p in paras]
        if strategy == "structured"
        else [TextBlock("\n\n".join(paras), "text")]
    )
    size = len(join_blocks(blocks))
    seen = {"calls": 0, "chars": 0}

    def tokens(value: str) -> int:
        seen["calls"] += 1
        seen["chars"] += len(value)
        return (len(value) + 3) // 4

    chunks = chunk_blocks(blocks, strategy, length_fn=tokens, max_length=128, chunk_size=10**6)
    assert all(tokens(c.text) <= 128 for c in chunks[:-1])
    seen["calls"] = seen["chars"] = 0
    chunk_blocks(blocks, strategy, length_fn=tokens, max_length=128, chunk_size=10**6)
    assert seen["calls"] / len(chunks) <= 6
    assert seen["chars"] <= 6 * size


def _word_count(value: str) -> int:
    return len(value.split())


def _quarter_chars(value: str) -> int:
    return (len(value) + 3) // 4


def _script_weighted(value: str) -> int:
    wide = sum(1 for ch in value if ord(ch) > 0x2E80)
    return wide + (len(value) - wide + 3) // 4


def _swinging_text(rng: random.Random, length: int) -> str:
    parts: List[str] = []
    total = 0
    while total < length:
        if rng.random() < 0.5:
            piece = _words(rng, rng.randint(20, 80)) + " "
        else:
            piece = "".join(rng.choice("这是一个用于测试的文本它没有标点") for _ in range(rng.randint(30, 200)))
        parts.append(piece)
        total += len(piece)
    return "".join(parts)[:length]


def _reference_fit(text: str, s: int, hi: int, budget: int, cap: int, measure) -> int:
    limit = min(hi, s + cap)
    best = s + 1
    for e in range(s + 1, limit + 1):
        if measure(text[s:e]) > budget:
            break
        best = e
    return best


@pytest.mark.parametrize("measure", [_word_count, _quarter_chars, _script_weighted])
@pytest.mark.parametrize("budget", [1, 3, 17, 120, 900])
def test_fit_matches_a_linear_reference_for_monotone_length_functions(measure, budget: int) -> None:
    from septum_api.services.chunking.cutting import Cutter

    for seed in range(8):
        rng = random.Random(seed)
        text = _swinging_text(rng, 2500)
        cap = rng.choice([40, 300, 10**6])
        cutter = Cutter(text, [], measure, cap)
        for _ in range(10):
            s = rng.randrange(0, len(text) - 1)
            hi = rng.choice([len(text), min(len(text), s + rng.randint(1, 700))])
            if hi <= s:
                continue
            assert cutter._fit(s, hi, budget) == _reference_fit(text, s, hi, budget, cap, measure), (seed, s, hi)


@pytest.mark.parametrize("measure", [_word_count, _script_weighted])
def test_fit_stays_exact_when_walking_a_text_whose_ratio_swings(measure) -> None:
    from septum_api.services.chunking.cutting import Cutter

    text = _swinging_text(random.Random(99), 4000)
    cutter = Cutter(text, [], measure, 10**6)
    s = 0
    while s < len(text) - 1:
        expected = _reference_fit(text, s, len(text), 25, 10**6, measure)
        assert cutter._fit(s, len(text), 25) == expected
        s = max(s + 1, expected - 3)


def _table_reference(blocks: List[TextBlock], cells: int, protected: Sequence[Range]) -> List[Range]:
    text = join_blocks(blocks)
    merged = merge_ranges(protected, len(text))
    lines: List[Tuple[int, str]] = []
    position = 0
    for line in text.split("\n"):
        lines.append((position, line))
        position += len(line) + 1
    starts: List[int] = []
    body_index = 0
    columns = 1
    for offset, line in lines:
        if not line:
            continue
        if line.startswith("# Sheet:"):
            starts.append(offset)
            body_index = 0
            continue
        if body_index == 0:
            columns = line.count("\t") + 1
        per_chunk = max(1, cells // columns)
        if body_index and body_index % per_chunk == 0:
            starts.append(offset)
        body_index += 1
    valid = [o for o in starts if not any(a < o < b for a, b in merged)]
    edges = sorted(set(valid) | {0, len(text)})
    return list(zip(edges, edges[1:]))


def test_table_boundaries_equal_a_line_based_reference_with_protected_row_starts() -> None:
    blocks = _sheet("Alpha", rows=23, columns=4) + _sheet("Beta", rows=11, columns=3)
    text = join_blocks(blocks)
    row_starts = [i + 1 for i, ch in enumerate(text) if ch == "\n"]
    cases = [
        [],
        [(row_starts[2] - 2, row_starts[2] + 2)],
        [(row_starts[3], row_starts[3] + 3)],
        [(row_starts[1] - 1, row_starts[5] + 1)],
        [(row_starts[24] - 5, row_starts[24] + 5), (row_starts[27], row_starts[28])],
    ]
    for rng_seed in range(25):
        rng = random.Random(rng_seed)
        picks = rng.sample(row_starts, rng.randint(1, 6))
        cases.append([(max(0, p - rng.randint(0, 3)), min(len(text), p + rng.randint(0, 3))) for p in picks])
    for cells in (4, 8, 12, 1000):
        for protected in cases:
            chunks = chunk_blocks(blocks, "table", spreadsheet_chunk_size=cells, protected=protected)
            assert [(c.start, c.end) for c in chunks] == _table_reference(blocks, cells, protected), (cells, protected)
            _assert_covers(chunks, text)


def _audio_reference(blocks: List[TextBlock], seconds: float, protected: Sequence[Range]) -> List[Range]:
    text = join_blocks(blocks)
    merged = merge_ranges(protected, len(text))
    starts: List[int] = []
    origin = None
    for i, block in enumerate(blocks):
        offset = len(join_blocks(blocks[: i + 1])) - len(block.text)
        if block.kind != "segment" or block.t_start is None or block.t_end is None:
            starts.append(offset)
            origin = None
        elif origin is None or block.t_end - origin > seconds:
            starts.append(offset)
            origin = block.t_start
    valid = [o for o in starts if not any(a < o < b for a, b in merged)]
    edges = sorted(set(valid) | {0, len(text)})
    return list(zip(edges, edges[1:]))


def test_audio_boundaries_equal_a_reference_with_protected_segment_starts() -> None:
    for seed in range(40):
        rng = random.Random(seed)
        blocks: List[TextBlock] = []
        clock = 0.0
        for n in range(rng.randint(3, 25)):
            length = rng.uniform(1.0, 40.0)
            body = f" segment {n} " + _words(rng, rng.randint(1, 8)) + " "
            if rng.random() < 0.1:
                blocks.append(TextBlock(body, "paragraph"))
            else:
                blocks.append(TextBlock(body, "segment", t_start=clock, t_end=clock + length))
            clock += length
        text = join_blocks(blocks)
        starts = [len(join_blocks(blocks[: i + 1])) - len(b.text) for i, b in enumerate(blocks)]
        protected = [(max(0, p - rng.randint(0, 4)), min(len(text), p + rng.randint(0, 4))) for p in rng.sample(starts, min(len(starts), rng.randint(0, 5)))]
        seconds = rng.choice([10.0, 30.0, 60.0])
        chunks = chunk_blocks(blocks, "audio", audio_chunk_size=seconds, protected=protected)
        assert [(c.start, c.end) for c in chunks] == _audio_reference(blocks, seconds, protected), (seed, protected)
        _assert_covers(chunks, text)
        for chunk in chunks:
            first = blocks[bisect_right(starts, chunk.start) - 1]
            assert chunk.t_start == first.t_start


def _header_offset(text: str, sheet: str) -> int:
    return text.index(f"# Sheet: {sheet}")


def test_table_title_offsets_cover_the_sheet_header_for_every_chunk() -> None:
    blocks = _sheet("Alpha", rows=15, columns=4) + _sheet("Beta", rows=9, columns=4)
    text = join_blocks(blocks)
    chunks = chunk_blocks(blocks, "table", spreadsheet_chunk_size=8)

    assert len(chunks) > 4
    for chunk in chunks:
        header_start = _header_offset(text, chunk.sheet)
        header_end = text.index("\n", header_start)
        assert (chunk.section_title_start, chunk.section_title_end) == (header_start, header_end)
        assert text[chunk.section_title_start : chunk.section_title_end] == chunk.section_title


def test_table_title_longer_than_the_limit_is_truncated_but_offsets_span_all_of_it() -> None:
    name = "N" * (MAX_SECTION_TITLE_CHARS + 188)
    blocks = _sheet(name, rows=12, columns=4) + _sheet("Short", rows=6, columns=4)
    text = join_blocks(blocks)
    chunks = chunk_blocks(blocks, "table", spreadsheet_chunk_size=8)

    long_header = blocks[0].text
    assert len(long_header) > MAX_SECTION_TITLE_CHARS
    long_chunks = [c for c in chunks if c.sheet == name]
    assert len(long_chunks) > 2
    for chunk in long_chunks:
        assert chunk.section_title == long_header[:MAX_SECTION_TITLE_CHARS]
        assert (chunk.section_title_start, chunk.section_title_end) == (0, len(long_header))
        assert text[chunk.section_title_start : chunk.section_title_end] == long_header
    short = [c for c in chunks if c.sheet == "Short"]
    assert all(c.section_title == blocks[-2].text or c.section_title.startswith("# Sheet: Short") for c in short)
    assert all(text[c.section_title_start : c.section_title_end].startswith(c.section_title) for c in chunks)


def test_structured_title_offsets_follow_the_heading_in_effect_including_one_starting_at_the_chunk() -> None:
    long_title = "H" * (MAX_SECTION_TITLE_CHARS + 300)
    blocks = [
        TextBlock("preamble " * 3, "paragraph"),
        TextBlock("First", "heading"),
        TextBlock("alpha " * 60, "paragraph"),
        TextBlock(long_title, "heading"),
        TextBlock("omega " * 60, "paragraph"),
    ]
    text = join_blocks(blocks)
    starts = [len(join_blocks(blocks[: i + 1])) - len(b.text) for i, b in enumerate(blocks)]
    chunks = chunk_blocks(blocks, "structured", chunk_size=MIN_CHUNK_SIZE, overlap=0)

    heading_spans = [(starts[1], starts[1] + len("First")), (starts[3], starts[3] + len(long_title))]
    for chunk in chunks:
        in_effect = [span for span in heading_spans if span[0] <= chunk.start]
        if not in_effect:
            assert (chunk.section_title, chunk.section_title_start, chunk.section_title_end) == (None, None, None)
            continue
        span = in_effect[-1]
        assert (chunk.section_title_start, chunk.section_title_end) == span
        assert chunk.section_title == text[span[0] : span[1]][:MAX_SECTION_TITLE_CHARS]
    assert any(c.start == starts[3] for c in chunks) or any(c.start > starts[3] for c in chunks)
    assert chunks[0].section_title is None


def test_sliding_and_audio_chunks_carry_no_title_offsets() -> None:
    sliding = chunk_blocks([TextBlock("Heading", "heading"), TextBlock("word " * 200, "paragraph")], "sliding", chunk_size=100, overlap=0)
    audio = chunk_blocks(_segments(5, 10.0), "audio", audio_chunk_size=10)
    assert all(c.section_title_start is None and c.section_title_end is None for c in sliding + audio)


def test_repr_omits_text_and_section_title() -> None:
    record = ChunkRecord(
        index=0, start=0, end=9, text="SECRET-BODY", strategy="structured",
        sheet="SECRET-SHEET", section_title="SECRET-TITLE",
        section_title_start=0, section_title_end=12,
    )
    shown = repr(record)
    assert "SECRET-BODY" not in shown and "SECRET-TITLE" not in shown
    assert "SECRET-SHEET" not in shown
    assert "start=0" in shown and "section_title_end=12" in shown


def _assert_cuts_outside_ranges(chunks: Sequence[ChunkRecord], ranges: Sequence[Range], length: int) -> None:
    merged = merge_ranges(ranges, length)
    for r in chunks:
        points = [("start", r.start), ("end", r.end)]
        if r.section_title:
            points += [
                ("title start", r.section_title_start),
                ("title cut", r.section_title_start + len(r.section_title)),
            ]
        for a, b in merged:
            for name, point in points:
                assert not a < point < b, (r.strategy, name, point, (a, b))


def _strategy_inputs(heading: str, body: str) -> List[Tuple[str, List[TextBlock]]]:
    structured = [
        TextBlock(body, "paragraph"), TextBlock(heading, "heading"),
        TextBlock(body, "paragraph"), TextBlock(heading, "heading"), TextBlock(body, "paragraph"),
    ]
    table = [
        TextBlock(heading, "sheet_header", sheet="S"),
        TextBlock("\n".join("\t".join(f"c{r}{c}" for c in range(3)) for r in range(30)), "rows", sheet="S"),
    ]
    audio = [TextBlock(f" {body[:40]} {n} ", "segment", t_start=n * 5.0, t_end=n * 5.0 + 5.0) for n in range(12)]
    return [("structured", structured), ("sliding", structured), ("table", table), ("audio", audio)]


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("heading", ["Head " * 130, "標題" * 300, "H"])
def test_no_offset_of_any_record_or_title_falls_strictly_inside_a_protected_range(seed: int, heading: str) -> None:
    rng = random.Random(seed)
    for strategy, blocks in _strategy_inputs(heading, "文字 text " * 40):
        text = join_blocks(blocks)
        n = len(text)
        title_starts = [text.index(b.text) for b in blocks if b.kind in ("heading", "sheet_header")]
        ts = title_starts[0] if title_starts else 0
        k = rng.randrange(1, n)
        ranges = _ranges(rng, n) + [(0, k), (k, n), (min(n, ts + 500), min(n, ts + 520)), (ts, min(n, ts + 512))]
        ranges += [(max(0, t - rng.randint(1, 30)), min(n, t + rng.randint(1, 30))) for t in title_starts]
        ranges += [(a, min(n, a + rng.randint(1, 5))) for a in range(0, n, rng.randint(2, 7))]
        for kwargs in ({}, {"max_length": 1, "length_fn": lambda s: len(s.encode())}):
            for protected in (ranges, ranges[-40:], [(0, k), (k, n)]):
                chunks = chunk_blocks(blocks, strategy, protected=protected, chunk_size=MIN_CHUNK_SIZE, overlap=20, **kwargs)
                _assert_cuts_outside_ranges(chunks, protected, n)


def test_heading_starting_inside_a_range_moves_the_title_to_the_range_end() -> None:
    blocks = [TextBlock("para " * 20, "paragraph"), TextBlock("Heading text", "heading"), TextBlock("body " * 40, "paragraph")]
    text = join_blocks(blocks)
    hs = text.index("Heading text")
    chunks = chunk_blocks(blocks, "structured", protected=[(hs - 5, hs + 4)], chunk_size=MIN_CHUNK_SIZE, overlap=0)
    titled = [c for c in chunks if c.section_title is not None]
    assert titled
    for c in titled:
        assert (c.section_title_start, c.section_title) == (hs + 4, "ing text")
        assert text[c.section_title_start : c.section_title_end] == "ing text"
