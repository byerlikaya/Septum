"""``project`` masks every chunk from document-level spans, including cut spans."""

from __future__ import annotations

import random
import re

import pytest
from septum_core import SeptumCoreConfig, SeptumEngine
from septum_core.anonymization_map import AnonymizationMap
from septum_core.detector import Detector
from septum_core.ports import NullSemanticDetectionPort
from septum_core.span_projection import ProjectedSpan, project, protected_ranges
from septum_core.spans import DetectedSpan, DetectionResult, ResolvedSpan

_CONFIG = SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=False)
_PLACEHOLDER = re.compile(r"\[[A-Z_]+_\d+\]")
_FILLER_WORDS = ("lorem", "ipsum", "dolor", "amet", "cras", "vivamus", "nunc", "praesent")
_VALUES = (
    "jane.doe@example.com",
    "bob.smith@example.org",
    "alice99@example.net",
    "+44 20 7946 0958",
    "+49 30 123456",
)
_MIXED_WORDS = (
    "Lorem", "Ipsum", "London", "Paris", "John", "Smith", "dolor", "amet", "Acme",
)
_SEEDS = range(40)


@pytest.fixture(scope="module")
def detector() -> Detector:
    engine = SeptumEngine(
        regulations=["gdpr", "kvkk"], config=_CONFIG, semantic_port=NullSemanticDetectionPort()
    )
    return engine._detector


def _random_document(
    rng: random.Random, words: tuple[str, ...] = _FILLER_WORDS
) -> tuple[str, list[tuple[int, int, str]]]:
    """Document text and the ``(start, end, value)`` of every injected value."""
    text = ""
    injected: list[tuple[int, int, str]] = []
    for _ in range(rng.randint(4, 9)):
        text += (" " if text else "") + " ".join(rng.choices(words, k=rng.randint(2, 12)))
        if rng.random() < 0.8:
            value = rng.choice(_VALUES)
            text += " "
            injected.append((len(text), len(text) + len(value), value))
            text += value
    return text, injected


def _random_ranges(rng: random.Random, length: int) -> list[tuple[int, int]]:
    ranges = []
    for _ in range(rng.randint(1, 8)):
        start = rng.randrange(0, length)
        ranges.append((start, rng.randint(start + 1, length)))
    return ranges


def _prepare(detector: Detector, text: str):
    detection = detector.detect(text, "en")
    anon_map = AnonymizationMap(document_id=1, language="en")
    result = detector.apply(detection, anon_map)
    return detection, anon_map, result


def _detection(text: str, spans=()) -> DetectionResult:
    """Detection whose spans are those ``apply`` would have resolved from ``spans``."""
    detected = tuple(
        DetectedSpan(s.start, s.end, s.entity_type, s.score, s.core) for s in spans
    )
    return DetectionResult(text=text, language="en", spans=detected, semantic_enabled=False)


def _span(start: int, end: int, placeholder: str = "[EMAIL_ADDRESS_1]", core=None) -> ResolvedSpan:
    return ResolvedSpan(start, end, "EMAIL_ADDRESS", placeholder, 1.0, core)


def _fresh_map() -> AnonymizationMap:
    return AnonymizationMap(document_id=1, language="en")


def _oracle_chunk(text, spans, chunk_start, chunk_end) -> str:
    """Expected masked slice, built per character from ``spans`` alone."""
    expected: list[str] = []
    for position in range(chunk_start, chunk_end):
        owner = next((s for s in spans if s.start <= position < s.end), None)
        if owner is None:
            expected.append(text[position])
        elif position == max(owner.start, chunk_start):
            expected.append(owner.placeholder)
    return "".join(expected)


@pytest.mark.parametrize("seed", _SEEDS)
def test_chunks_equal_reference_oracle(detector, seed) -> None:
    rng = random.Random(seed)
    text, injected = _random_document(rng, _MIXED_WORDS)
    detection, anon_map, result = _prepare(detector, text)
    assert detection.text == text
    for start, end, value in injected:
        found = [s for s in result.detected_spans if (s.start, s.end) == (start, end)]
        assert len(found) == 1, f"detector missed {value!r} at {start}"
    ranges = _random_ranges(rng, len(text)) + [(0, len(text))]

    chunks = project(detection, result.detected_spans, ranges, anon_map)

    assert [(c.start, c.end) for c in chunks] == ranges
    for chunk in chunks:
        assert chunk.masked_text == _oracle_chunk(
            text, result.detected_spans, chunk.start, chunk.end
        )


@pytest.mark.parametrize("seed", _SEEDS)
def test_chunks_equal_reference_oracle_lowercase_exact_count(detector, seed) -> None:
    rng = random.Random(seed)
    text, injected = _random_document(rng)
    detection, anon_map, result = _prepare(detector, text)
    assert [(s.start, s.end) for s in result.detected_spans] == [(a, b) for a, b, _ in injected]
    ranges = _random_ranges(rng, len(text)) + [(0, len(text))]

    chunks = project(detection, result.detected_spans, ranges, anon_map)

    for chunk in chunks:
        assert chunk.masked_text == _oracle_chunk(
            text, result.detected_spans, chunk.start, chunk.end
        )


@pytest.mark.parametrize("seed", _SEEDS)
def test_same_value_same_placeholder_across_chunks(detector, seed) -> None:
    rng = random.Random(seed)
    text = _random_document(rng)[0] + " " + "jane.doe@example.com tail"
    detection, anon_map, result = _prepare(detector, text)
    ranges = _random_ranges(rng, len(detection.text))
    ranges.append((0, len(detection.text)))

    chunks = project(detection, result.detected_spans, ranges, anon_map)

    by_value: dict[str, str] = {}
    for span in result.detected_spans:
        value = detection.text[span.start : span.end]
        assert by_value.setdefault(value, span.placeholder) == span.placeholder
    assert len(set(by_value.values())) == len(by_value)
    for chunk in chunks:
        for span in result.detected_spans:
            if span.start < chunk.end and span.end > chunk.start:
                assert by_value[detection.text[span.start : span.end]] in chunk.masked_text


@pytest.mark.parametrize("seed", _SEEDS)
def test_full_text_chunk_equals_sanitize(detector, seed) -> None:
    text = _random_document(random.Random(seed))[0]
    detection, anon_map, result = _prepare(detector, text)

    (chunk,) = project(detection, result.detected_spans, [(0, len(detection.text))], anon_map)

    expected = detector.sanitize(text, "en", AnonymizationMap(document_id=1, language="en"))
    assert chunk.masked_text == expected.sanitized_text == result.sanitized_text
    assert len(chunk.spans) == len(result.detected_spans)


def test_cut_span_shows_full_placeholder_in_both_chunks(detector) -> None:
    text = "lorem jane.doe@example.com ipsum"
    detection, anon_map, result = _prepare(detector, text)
    (span,) = result.detected_spans
    cut = (span.start + span.end) // 2

    left, right = project(
        detection,
        result.detected_spans,
        [(0, cut), (cut, len(detection.text))],
        anon_map,
    )

    assert left.masked_text == f"lorem {span.placeholder}"
    assert right.masked_text == f"{span.placeholder} ipsum"
    assert (left.spans[0].start, left.spans[0].end) == (span.start, cut)
    assert (right.spans[0].start, right.spans[0].end) == (0, span.end - cut)
    assert left.spans[0].clipped and right.spans[0].clipped


def test_every_interior_cut_of_one_email_masks_both_sides(detector) -> None:
    email = "jane.doe@example.com"
    text = f"lorem {email} ipsum"
    detection, anon_map, result = _prepare(detector, text)
    (span,) = result.detected_spans

    for cut in range(span.start + 1, span.end):
        left, right = project(
            detection, [span], [(0, cut), (cut, len(text))], anon_map
        )
        assert left.masked_text == f"lorem {span.placeholder}"
        assert right.masked_text == f"{span.placeholder} ipsum"
        for chunk in (left, right):
            assert not any(c in _PLACEHOLDER.sub("", chunk.masked_text) for c in "@.")
            assert "jane" not in chunk.masked_text and "example" not in chunk.masked_text


@pytest.mark.parametrize("touch", ["span_end_at_chunk_start", "span_start_at_chunk_end"])
def test_span_touching_chunk_only_at_edge_is_absent(touch) -> None:
    text = "abcdefghij"
    span = _span(3, 6)
    chunk = (6, 10) if touch == "span_end_at_chunk_start" else (0, 3)

    (result,) = project(_detection(text, [span]), [span], [chunk], _fresh_map())

    assert span.placeholder not in result.masked_text
    assert result.masked_text == text[chunk[0] : chunk[1]]
    assert result.spans == ()


def test_chunk_strictly_inside_one_span() -> None:
    (chunk,) = project(_detection("abcdefghij", [_span(2, 9)]), [_span(2, 9)], [(4, 7)], _fresh_map())

    assert chunk.masked_text == "[EMAIL_ADDRESS_1]"
    assert len(chunk.spans) == 1
    assert chunk.spans[0] == ProjectedSpan(
        0, 3, "EMAIL_ADDRESS", "[EMAIL_ADDRESS_1]", 1.0, None, True, False
    )


def test_shuffled_spans_equal_sorted_result() -> None:
    text = "a" * 40
    spans = [_span(i * 8 + 1, i * 8 + 5, f"[EMAIL_ADDRESS_{i}]") for i in range(5)]
    ranges = [(0, 40), (3, 20), (10, 33)]
    expected = project(_detection(text, spans), spans, ranges, _fresh_map())

    for seed in range(10):
        shuffled = spans[:]
        random.Random(seed).shuffle(shuffled)
        assert project(_detection(text, spans), shuffled, ranges, _fresh_map()) == expected


def test_overlapping_unsorted_ranges_are_independent_and_in_range_order() -> None:
    text = "0123456789"
    span = _span(4, 6)
    ranges = [(5, 10), (0, 5), (2, 8), (0, 10)]

    chunks = project(_detection(text, [span]), [span], ranges, _fresh_map())

    assert [(c.start, c.end) for c in chunks] == ranges
    assert [c.masked_text for c in chunks] == [
        "[EMAIL_ADDRESS_1]6789",
        "0123[EMAIL_ADDRESS_1]",
        "23[EMAIL_ADDRESS_1]67",
        "0123[EMAIL_ADDRESS_1]6789",
    ]


def test_adjacent_touching_spans_both_masked_in_order() -> None:
    spans = [_span(5, 8, "[EMAIL_ADDRESS_2]"), _span(2, 5, "[EMAIL_ADDRESS_1]")]

    (chunk,) = project(_detection("0123456789", spans), spans, [(0, 10)], _fresh_map())

    assert chunk.masked_text == "01[EMAIL_ADDRESS_1][EMAIL_ADDRESS_2]89"
    assert [s.placeholder for s in chunk.spans] == ["[EMAIL_ADDRESS_1]", "[EMAIL_ADDRESS_2]"]


@pytest.mark.parametrize(
    ("core", "chunk", "expected_core", "core_outside"),
    [
        ((4, 8), (5, 12), (0, 3), False),
        ((4, 8), (0, 4), None, True),
        ((4, 8), (8, 12), None, True),
        ((4, 8), (0, 5), (4, 5), False),
        (None, (5, 12), None, False),
        ((2, 10), (2, 10), (0, 8), False),
    ],
)
def test_core_clipping(core, chunk, expected_core, core_outside) -> None:
    span = _span(2, 10, "[PERSON_NAME_1]", core)

    (result,) = project(_detection("x" * 20, [span]), [span], [chunk], _fresh_map())

    (projected,) = result.spans
    assert projected.core == expected_core
    assert projected.core_outside is core_outside


def test_core_is_clipped_and_shifted() -> None:
    span = _span(2, 10, "[PERSON_NAME_1]", (4, 8))

    (chunk,) = project(_detection("x" * 20, [span]), [span], [(5, 12)], _fresh_map())

    assert chunk.spans[0].core == (0, 3)
    assert (chunk.spans[0].start, chunk.spans[0].end) == (0, 5)


def test_protected_ranges_merge_touching_spans() -> None:
    spans = [_span(10, 14), _span(1, 4), _span(4, 6), _span(20, 22)]

    assert protected_ranges(_detection("x" * 30, spans), spans, _fresh_map()) == [
        (1, 6),
        (10, 14),
        (20, 22),
    ]
    assert protected_ranges(_detection("x" * 30), [], _fresh_map()) == []


@pytest.mark.parametrize(
    ("name", "text", "residual"),
    [
        ("Johnson Smithers", "Johnson Smithers met. Later Smithers left.", "Smithers left"),
        ("Jean-Pierre Martin", "Jean-Pierre Martin met. Later jean-pierre left.", "jean-pierre left"),
    ],
)
def test_residual_blocklisted_mention_never_leaks_fragment(name, text, residual) -> None:
    anon_map = _fresh_map()
    placeholder = anon_map.add_entity(name, "PERSON_NAME")
    spans = [_span(0, len(name), placeholder)]
    detection = _detection(text, spans)
    start = text.index(residual)
    end = start + len(residual.split()[0])
    (whole,) = project(detection, spans, [(0, len(text))], anon_map)
    assert text[start:end] not in whole.masked_text
    replacement = whole.masked_text.split(" Later ")[1].split(" left")[0]
    assert _PLACEHOLDER.fullmatch(replacement)

    for cut in range(start + 1, end):
        left, right = project(detection, spans, [(0, cut), (cut, len(text))], anon_map)
        assert not left.masked_text.endswith(text[start:cut])
        assert not right.masked_text.startswith(text[cut:end])
        assert left.masked_text.endswith(" Later " + replacement)
        assert right.masked_text.startswith(replacement + " left")
    assert protected_ranges(detection, spans, anon_map)[-1] == (start, end)


def test_language_is_read_from_detection_not_passed() -> None:
    with pytest.raises(TypeError):
        project(_detection("text"), [], [(0, 2)], _fresh_map(), **{"language": "en"})


def test_span_outside_detection_text_raises() -> None:
    with pytest.raises(ValueError):
        project(_detection("short", [_span(2, 9)]), [_span(2, 9)], [(0, 5)], _fresh_map())


@pytest.mark.parametrize(
    "bad", [(-1, 3), (3, 3), (5, 3), (0, 99), (99, 100)]
)
def test_invalid_range_raises_before_any_output(bad) -> None:
    with pytest.raises(ValueError):
        project(_detection("0123456789"), [], [(0, 5), bad], _fresh_map())


@pytest.mark.parametrize("bad", [(0.0, 5), (0, 5.0), (True, 5), (0, True), ("1", 5), (None, 5)])
def test_non_integer_range_bounds_are_rejected(bad) -> None:
    with pytest.raises(TypeError):
        project(_detection("0123456789"), [], [bad], _fresh_map())


@pytest.mark.parametrize("bad", [(1.0, 4), (1, 4.0), (True, 4)])
def test_non_integer_span_bounds_are_rejected(bad) -> None:
    with pytest.raises(TypeError):
        project(_detection("0123456789", [_span(*bad)]), [_span(*bad)], [(0, 10)], _fresh_map())


@pytest.mark.parametrize("bad", [(-1, 3), (3, 3), (4, 99), (2, 6)])
def test_invalid_or_overlapping_spans_raise(bad) -> None:
    good = _span(1, 4)
    other = _span(bad[0], bad[1], "[EMAIL_ADDRESS_2]")

    with pytest.raises(ValueError):
        project(_detection("0123456789", [good, other]), [good, other], [(0, 10)], _fresh_map())


def test_clipped_flag_distinguishes_exact_fit_from_one_sided_cut() -> None:
    text = "abcdefghij"
    span = _span(3, 6)
    detection = _detection(text, [span])

    exact, left_cut, right_cut, wider = project(
        detection, [span], [(3, 6), (3, 5), (4, 6), (2, 8)], _fresh_map()
    )

    assert exact.spans[0].clipped is False
    assert left_cut.spans[0].clipped is True
    assert right_cut.spans[0].clipped is True
    assert wider.spans[0].clipped is False


@pytest.mark.parametrize(
    ("core", "expected_core"),
    [((4, 6), (2, 8)), ((5, 5), (2, 8)), ((0, 3), (2, 3)), ((9, 12), (2, 8))],
)
def test_core_that_apply_ignores_falls_back_to_whole_range(core, expected_core) -> None:
    text = "xxcd  efxx"
    span = _span(2, 8, "[PERSON_NAME_1]", core)

    (chunk,) = project(_detection(text, [span]), [span], [(0, len(text))], _fresh_map())

    assert chunk.spans[0].core == expected_core
    assert chunk.spans[0].core_outside is False


def test_residual_mention_starting_at_span_end_is_redacted() -> None:
    text = "Johnson,Smithers left."
    anon_map = _fresh_map()
    placeholder = anon_map.add_entity("Johnson Smithers", "PERSON_NAME")
    spans = [_span(0, 8, placeholder)]

    (chunk,) = project(_detection(text, spans), spans, [(0, len(text))], anon_map)

    assert "mithers" not in chunk.masked_text
    assert _PLACEHOLDER.sub("", chunk.masked_text) == " left."


def test_residual_mention_before_the_minting_span_is_redacted() -> None:
    text = "Smithers said hi. Johnson Smithers left."
    anon_map = _fresh_map()
    placeholder = anon_map.add_entity("Johnson Smithers", "PERSON_NAME")
    start = text.index("Johnson")
    spans = [_span(start, start + len("Johnson Smithers"), placeholder)]

    (chunk,) = project(_detection(text, spans), spans, [(0, len(text))], anon_map)

    assert "Smithers" not in chunk.masked_text
    assert _PLACEHOLDER.sub("#", chunk.masked_text) == "# said hi. # left."


@pytest.mark.parametrize("shift", [1, -1])
def test_shifted_resolved_span_fails_closed(shift) -> None:
    text = "Mail jane.doe@example.com now."
    detected = _span(5, 25)
    shifted = _span(5 + shift, 25 + shift)

    with pytest.raises(ValueError):
        project(_detection(text, [detected]), [shifted], [(0, len(text))], _fresh_map())
    with pytest.raises(ValueError):
        protected_ranges(_detection(text, [detected]), [shifted], _fresh_map())


def test_missing_resolved_span_fails_closed() -> None:
    text = "Mail jane.doe@example.com now."

    with pytest.raises(ValueError):
        project(_detection(text, [_span(5, 25)]), [], [(0, len(text))], _fresh_map())
    with pytest.raises(ValueError):
        protected_ranges(_detection(text, [_span(5, 25)]), [], _fresh_map())


def test_resolved_span_without_detection_fails_closed() -> None:
    text = "Mail jane.doe@example.com now."

    with pytest.raises(ValueError):
        project(_detection(text), [_span(5, 25)], [(0, len(text))], _fresh_map())


def test_spans_apply_legitimately_drops_are_accepted() -> None:
    text = "ab   cdefghij"
    kept = _span(0, 2, "[EMAIL_ADDRESS_1]")
    detection = DetectionResult(
        text=text,
        language="en",
        spans=(
            DetectedSpan(0, 2, "EMAIL_ADDRESS", 1.0),
            DetectedSpan(1, 4, "EMAIL_ADDRESS", 0.5),
            DetectedSpan(2, 5, "EMAIL_ADDRESS", 0.5),
        ),
        semantic_enabled=False,
    )

    (chunk,) = project(detection, [kept], [(0, len(text))], _fresh_map())

    assert chunk.masked_text == "[EMAIL_ADDRESS_1]   cdefghij"


def _reference_project(detection, spans, anon_map, chunk_start, chunk_end):
    """Masked text and projected spans from a full scan of every edit, no bisect."""
    from septum_core.span_projection import _edits, _projected

    text = detection.text
    parts: list[str] = []
    projected = []
    cursor = chunk_start
    for edit in _edits(detection, sorted(spans, key=lambda s: s.start), anon_map):
        if edit.end <= chunk_start or edit.start >= chunk_end:
            continue
        parts.append(text[cursor : max(edit.start, chunk_start)])
        parts.append(edit.text)
        if edit.span is not None:
            projected.append(_projected(edit.span, text, chunk_start, chunk_end))
        cursor = min(edit.end, chunk_end)
    parts.append(text[cursor:chunk_end])
    return "".join(parts), tuple(projected)


def _synthetic_document(rng: random.Random, known_name: str):
    """Text, spans (adjacent, single-character and wide) and a map that knows ``known_name``."""
    pieces: list[str] = []
    spans: list[ResolvedSpan] = []
    length = 0
    for number in range(rng.randint(1, 14)):
        gap = rng.choice(["", "", " ", "ab ", "  ", " " + known_name + " ", known_name])
        pieces.append(gap)
        length += len(gap)
        width = rng.choice([1, 1, 2, 5, 12])
        pieces.append("Q" * width)
        spans.append(_span(length, length + width, f"[EMAIL_ADDRESS_{number + 1}]"))
        length += width
    pieces.append(rng.choice(["", " tail", " " + known_name]))
    return "".join(pieces), spans


@pytest.mark.parametrize("seed", range(60))
def test_projection_equals_a_full_scan_reference_for_random_ranges(seed) -> None:
    rng = random.Random(seed)
    known = "Johnson"
    text, spans = _synthetic_document(rng, known)
    anon_map = _fresh_map()
    anon_map.add_entity(known, "PERSON_NAME")
    detection = _detection(text, spans)
    edge_points = sorted(
        {0, len(text)}
        | {s.start for s in spans}
        | {s.end for s in spans}
        | {p + d for s in spans for p in (s.start, s.end) for d in (-1, 1) if 0 <= p + d <= len(text)}
    )
    ranges = [(0, len(text)), (0, 1), (len(text) - 1, len(text))]
    for _ in range(60):
        points = edge_points if rng.random() < 0.6 else range(len(text) + 1)
        start = rng.choice(points)
        end = rng.choice([p for p in points if p > start] or [len(text)])
        if start < end:
            ranges.append((start, end))

    chunks = project(detection, spans, ranges, anon_map)

    assert [(c.start, c.end) for c in chunks] == ranges
    for chunk in chunks:
        masked, projected = _reference_project(detection, spans, anon_map, chunk.start, chunk.end)
        assert chunk.masked_text == masked
        assert chunk.spans == projected
        assert [c.masked_text for c in project(detection, spans, [(chunk.start, chunk.end)], anon_map)] == [masked]
