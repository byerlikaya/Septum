"""Windowed detection of very large texts equals whole-document detection."""

from __future__ import annotations

import random
import unicodedata
from dataclasses import dataclass, field

import pytest
from presidio_analyzer import EntityRecognizer
from presidio_analyzer.predefined_recognizers import SpacyRecognizer

from septum_core import SeptumCoreConfig, SeptumEngine
from septum_core import detector as detector_module
from septum_core.anonymization_map import AnonymizationMap
from septum_core.detector import Detector
from septum_core.document_windowing import (
    DEFAULT_MAX_WHOLE_CHARS,
    DEFAULT_OVERLAP_CHARS,
    DEFAULT_WINDOW_CHARS,
    TextWindow,
    WindowingPolicy,
    merge_window_spans,
    plan_text_windows,
)
from septum_core.non_pii_filter import NonPiiFilter
from septum_core.ports import NullSemanticDetectionPort
from septum_core.recognizers import BUILTIN_REGULATION_IDS
from septum_core.regulations.composer import PolicyComposer
from septum_core.span_projection import project, protected_ranges
from septum_core.spans import DetectedSpan

_CONFIG = SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=False)
_SMALL = WindowingPolicy(max_whole_chars=300, window_chars=1000, overlap_chars=100)
_NARROW = WindowingPolicy(max_whole_chars=300, window_chars=1000, overlap_chars=10)
_WORDS = ("lorem", "ipsum", "dolor", "amet", "cras", "vivamus", "nunc", "praesent")
_VALUES = (
    "jane.doe@example.com",
    "bob.smith@example.org",
    "+44 20 7946 0958",
    "GB82WEST12345698765432",
)


@pytest.fixture(scope="module")
def detector() -> Detector:
    engine = SeptumEngine(
        regulations=["gdpr", "kvkk"], config=_CONFIG, semantic_port=NullSemanticDetectionPort()
    )
    return engine._detector


def _spans(detector: Detector, text: str, **kwargs) -> list[tuple[int, int, str]]:
    result = detector.detect(text, "en", **kwargs)
    return sorted((s.start, s.end, s.entity_type) for s in result.spans)


def _document(rng: random.Random, size: int) -> str:
    parts: list[str] = []
    length = 0
    while length < size:
        chunk = " ".join(rng.choices(_WORDS, k=rng.randint(3, 15)))
        if rng.random() < 0.3:
            chunk += " " + rng.choice(_VALUES)
        parts.append(chunk)
        length += len(chunk) + 1
    return "\n".join(parts)


def test_constants_are_consistent() -> None:
    assert DEFAULT_MAX_WHOLE_CHARS == 1_000_000
    assert DEFAULT_WINDOW_CHARS <= DEFAULT_MAX_WHOLE_CHARS
    assert DEFAULT_WINDOW_CHARS >= 4 * DEFAULT_OVERLAP_CHARS
    WindowingPolicy()


@pytest.mark.parametrize(
    ("kwargs"),
    [
        {"overlap_chars": 0},
        {"window_chars": 100, "overlap_chars": 30},
        {"max_whole_chars": -1},
    ],
)
def test_policy_rejects_invalid_values(kwargs) -> None:
    with pytest.raises(ValueError):
        WindowingPolicy(**kwargs)


def test_short_text_is_one_window() -> None:
    assert plan_text_windows("abc", _SMALL) == [TextWindow(0, 3, True, True)]


@pytest.mark.parametrize("seed", range(10))
def test_plan_covers_text_with_required_overlap(seed: int) -> None:
    text = _document(random.Random(seed), 9000)
    windows = plan_text_windows(text, _SMALL)

    assert windows[0].start == 0 and windows[0].first
    assert windows[-1].end == len(text) and windows[-1].last
    assert [w.first for w in windows] == [True] + [False] * (len(windows) - 1)
    for left, right in zip(windows, windows[1:], strict=False):
        assert right.start > left.start
        assert left.end - right.start >= _SMALL.overlap_chars
        assert right.start < left.end
    assert all(w.end - w.start <= _SMALL.window_chars for w in windows)


def test_plan_cuts_on_whitespace_not_inside_words() -> None:
    text = " ".join(["word"] * 1000)
    for window in plan_text_windows(text, _SMALL)[:-1]:
        assert text[window.end - 1] == " "
    for window in plan_text_windows(text, _SMALL)[1:]:
        assert text[window.start - 1] == " "


def test_plan_prefers_newlines_over_other_whitespace() -> None:
    text = "".join("aaaa bbbb\n" if i % 2 else "aaaa bbbb " for i in range(300))
    for window in plan_text_windows(text, _SMALL)[:-1]:
        assert text[window.end - 1] == "\n"


def test_plan_respects_boundary_hints() -> None:
    text = "x" * 3000
    hints = [1000 - 37, 1850]
    windows = plan_text_windows(text, _SMALL, hints)
    assert windows[0].end == 963
    assert any(w.end == 1850 for w in windows)


def test_plan_ignores_hints_outside_the_search_range() -> None:
    text = "x" * 3000
    near_start = plan_text_windows(text, _SMALL, [10, 400])
    assert near_start[0].end == 1000


def test_plan_never_cuts_inside_a_three_char_combining_cluster() -> None:
    text = "q\u0323\u0301" * 2000
    windows = plan_text_windows(text, _SMALL)
    assert len(windows) > 3
    for window in windows:
        assert window.end - window.start <= _SMALL.window_chars
        for edge in (window.start, window.end):
            if 0 < edge < len(text):
                assert not unicodedata.category(text[edge]).startswith("M")


def test_plan_takes_a_hint_exactly_at_the_target() -> None:
    text = "x" * 990 + "\n" + "x" * 2009
    assert plan_text_windows(text, _SMALL, [1000])[0].end == 1000


@pytest.mark.parametrize("seed", range(12))
def test_windowed_spans_equal_unwindowed(detector, seed: int) -> None:
    text = _document(random.Random(seed), 6000)
    whole = _spans(detector, text)
    assert whole

    assert _spans(detector, text, windowing=_SMALL) == whole


def test_below_threshold_is_unchanged(detector) -> None:
    text = _document(random.Random(1), 900)
    policy = WindowingPolicy(max_whole_chars=len(text), window_chars=1000, overlap_chars=100)
    assert detector.detect(text, "en", windowing=policy) == detector.detect(text, "en")


@pytest.mark.parametrize("separator", [" ", ";"])
def test_entity_straddling_every_window_edge_is_found_once_and_whole(
    detector, separator: str
) -> None:
    value = "jane.doe@example.com"
    filler = separator.join(["abc"] * 2000)
    for edge in (1000, 1900):
        for position in range(edge - len(value) - 2, edge + 3):
            text = (
                filler[:position] + separator + value + separator + filler[position:]
            )[:2600]
            windowed = _spans(detector, text, windowing=_SMALL)
            whole = _spans(detector, text)
            start = text.index(value)
            emails = [(s, e) for s, e, kind in windowed if kind == "EMAIL_ADDRESS"]
            assert emails == [(start, start + len(value))], (separator, edge, position)
            assert emails == [(s, e) for s, e, kind in whole if kind == "EMAIL_ADDRESS"]
            if separator == " ":
                assert windowed == whole, (edge, position)
            else:
                assert set(whole) <= set(windowed), (edge, position)


def test_merge_keeps_an_overlap_duplicate_once() -> None:
    windows = [TextWindow(0, 100, True, False), TextWindow(60, 160, False, True)]
    span = DetectedSpan(start=70, end=80, entity_type="EMAIL_ADDRESS", score=0.8)
    again = DetectedSpan(start=10, end=20, entity_type="EMAIL_ADDRESS", score=0.9)

    merged = merge_window_spans(windows, [[span], [again]])

    assert [(s.start, s.end, s.score) for s in merged] == [(70, 80, 0.9)]


def test_merge_drops_a_fragment_covered_by_a_whole_span() -> None:
    windows = [TextWindow(0, 100, True, False), TextWindow(60, 160, False, True)]
    fragment = DetectedSpan(start=90, end=100, entity_type="URL", score=0.9)
    whole = DetectedSpan(start=30, end=50, entity_type="URL", score=0.9)

    merged = merge_window_spans(windows, [[fragment], [whole]])

    assert [(s.start, s.end) for s in merged] == [(90, 110)]


def test_merge_keeps_fragments_of_an_entity_longer_than_the_overlap() -> None:
    windows = [TextWindow(0, 100, True, False), TextWindow(60, 160, False, True)]
    left = DetectedSpan(start=50, end=100, entity_type="URL", score=0.9)
    right = DetectedSpan(start=0, end=50, entity_type="URL", score=0.9)

    merged = merge_window_spans(windows, [[left], [right]])

    assert [(s.start, s.end) for s in merged] == [(50, 100), (60, 110)]


def test_merge_shifts_core_with_the_span() -> None:
    windows = [TextWindow(0, 100, True, False), TextWindow(60, 160, False, True)]
    span = DetectedSpan(start=20, end=40, entity_type="PERSON_NAME", score=0.9, core=(20, 35))

    (merged,) = merge_window_spans(windows, [[], [span]])

    assert (merged.start, merged.end, merged.core) == (80, 100, (80, 95))


def test_duplicate_matches_in_overlap_are_masked_once(detector) -> None:
    text = _document(random.Random(3), 6000)
    result = detector.detect(text, "en", windowing=_SMALL)
    keys = [(s.start, s.end) for s in result.spans]

    assert len(keys) == len(set(keys))
    ordered = sorted(keys)
    assert all(a[1] <= b[0] for a, b in zip(ordered, ordered[1:], strict=False))


def test_decomposed_text_is_normalized_once_before_windowing(detector) -> None:
    decomposed = "café q́ "
    text = (decomposed * 60 + "jane.doe@example.com " + decomposed * 60) * 3
    result = detector.detect(text, "en", windowing=_SMALL)

    assert result.text == unicodedata.normalize("NFC", text)
    assert len(result.text) < len(text)
    assert _spans(detector, text, windowing=_SMALL) == _spans(detector, text)
    for span in result.spans:
        assert "@" in result.text[span.start : span.end]


@pytest.mark.parametrize("seed", range(4))
def test_hints_only_move_cuts_not_results(detector, seed: int) -> None:
    rng = random.Random(seed)
    text = _document(rng, 6000)
    hints = sorted(rng.sample(range(1, len(text)), 40))

    assert _spans(detector, text, windowing=_SMALL, boundaries=hints) == _spans(detector, text)


def test_hints_are_ignored_when_normalization_changes_the_text(detector) -> None:
    text = ("café " * 400) + "jane.doe@example.com " + ("café " * 400)
    assert _spans(detector, text, windowing=_SMALL, boundaries=[5, 700, 1500]) == _spans(
        detector, text
    )


@pytest.mark.parametrize("seed", range(6))
def test_windowed_apply_and_project_equal_unwindowed(detector, seed: int) -> None:
    rng = random.Random(seed)
    text = _document(rng, 6000)
    ranges = []
    for _ in range(6):
        start = rng.randrange(0, len(text) - 1)
        ranges.append((start, rng.randint(start + 1, min(len(text), start + 900))))

    outputs = []
    for policy in (WindowingPolicy(), _SMALL):
        anon_map = AnonymizationMap(document_id=1, language="en")
        detection = detector.detect(text, "en", windowing=policy)
        result = detector.apply(detection, anon_map)
        chunks = project(detection, result.detected_spans, ranges, anon_map)
        outputs.append(
            (
                result.sanitized_text,
                [(c.start, c.end, c.masked_text) for c in chunks],
                protected_ranges(detection, result.detected_spans, anon_map),
            )
        )

    assert outputs[0] == outputs[1]


_W = [TextWindow(0, 100, True, False), TextWindow(60, 160, False, True)]


def _span(start: int, end: int, entity_type: str = "URL", score: float = 0.9, core=None):
    return DetectedSpan(start=start, end=end, entity_type=entity_type, score=score, core=core)


def test_entity_longer_than_the_overlap_stays_covered_despite_an_inner_whole_span() -> None:
    left = _span(50, 100, "CUSTOM")
    right = _span(0, 50, "CUSTOM")
    inner = _span(5, 15, "EMAIL_ADDRESS")

    merged = merge_window_spans(_W, [[left], [right, inner]])

    covered: set[int] = set()
    for found in merged:
        covered.update(range(found.start, found.end))
    assert set(range(50, 110)) <= covered
    assert (65, 75) in [(s.start, s.end) for s in merged]


def test_merge_keeps_same_range_different_type_and_core() -> None:
    a = _span(70, 80, "EMAIL_ADDRESS", core=(70, 80))
    b = _span(70, 80, "URL", core=(70, 80))
    c = _span(70, 80, "URL", core=(72, 80))

    assert len(merge_window_spans(_W, [[a, b, c], []])) == 3


def test_fragment_adjacent_to_a_whole_span_is_kept() -> None:
    merged = merge_window_spans(_W, [[_span(90, 100)], [_span(10, 30)]])

    assert [(s.start, s.end) for s in merged] == [(70, 90), (90, 100)]


def test_fragment_inside_a_long_whole_span_is_dropped() -> None:
    merged = merge_window_spans(_W, [[_span(5, 95)], [_span(0, 30)]])

    assert [(s.start, s.end) for s in merged] == [(5, 95)]


def test_span_at_text_start_is_not_a_fragment() -> None:
    merged = merge_window_spans(_W, [[_span(0, 30), _span(20, 40)], []])

    assert [(s.start, s.end) for s in merged] == [(0, 30), (20, 40)]


def test_span_at_text_end_is_not_a_fragment() -> None:
    merged = merge_window_spans(_W, [[], [_span(90, 100), _span(70, 95)]])

    assert [(s.start, s.end) for s in merged] == [(130, 155), (150, 160)]


def test_hints_reach_the_planner_only_when_the_text_is_already_normalized(
    detector, monkeypatch
) -> None:
    seen: list[tuple[int, ...]] = []
    real = detector_module.plan_text_windows

    def spy(text, policy, boundaries=()):
        seen.append(tuple(boundaries))
        return real(text, policy, boundaries)

    monkeypatch.setattr(detector_module, "plan_text_windows", spy)
    composed = "caf\u00e9 " * 400
    decomposed = unicodedata.normalize("NFD", composed)
    detector.detect(decomposed, "en", windowing=_SMALL, boundaries=[5, 700])
    detector.detect(composed, "en", windowing=_SMALL, boundaries=[5, 700])

    assert seen == [(), (5, 700)]


def test_threshold_is_exclusive(detector, monkeypatch) -> None:
    calls: list[int] = []
    real = detector_module.plan_text_windows

    def spy(*args):
        calls.append(1)
        return real(*args)

    monkeypatch.setattr(detector_module, "plan_text_windows", spy)
    text = "abc " * 100
    at_limit = WindowingPolicy(max_whole_chars=len(text), window_chars=400, overlap_chars=100)
    detector.detect(text, "en", windowing=at_limit)
    assert calls == []
    over = WindowingPolicy(max_whole_chars=len(text) - 1, window_chars=400, overlap_chars=100)
    detector.detect(text, "en", windowing=over)
    assert calls == [1]


def test_filter_and_resolve_run_once_on_the_full_text(detector, monkeypatch) -> None:
    text = "abc " * 500
    seen: dict[str, list[int]] = {"filter": [], "resolve": []}
    real_filter = detector._apply_non_pii_filter
    real_resolve = detector._resolve_overlaps

    def counting_filter(spans, normalized, language):
        seen["filter"].append(len(normalized))
        return real_filter(spans, normalized, language)

    def counting_resolve(spans, normalized, language):
        seen["resolve"].append(len(normalized))
        return real_resolve(spans, normalized, language)

    monkeypatch.setattr(detector, "_apply_non_pii_filter", counting_filter)
    monkeypatch.setattr(detector, "_resolve_overlaps", counting_resolve)
    detector.detect(text, "en", windowing=_SMALL)

    assert seen == {"filter": [len(text)], "resolve": [len(text)]}


class _ReversingFilter:
    def filter_spans(self, text, language, spans):
        return list(reversed(spans))


class _PassthroughFilter:
    def filter_spans(self, text, language, spans):
        return list(spans)


def test_non_pii_filter_lookup_keeps_first_duplicate_and_filter_order(
    detector, monkeypatch
) -> None:
    first = _span(0, 4, "PERSON_NAME", 0.9, core=(0, 2))
    twin = _span(0, 4, "PERSON_NAME", 0.9, core=(1, 3))
    lower = _span(0, 4, "PERSON_NAME", 0.5, core=(0, 4))
    monkeypatch.setattr(detector, "_non_pii_filter", _ReversingFilter())

    out = detector._apply_non_pii_filter([first, twin, lower], "abcd", "en")

    assert [(s.score, s.core) for s in out] == [(0.5, (0, 4)), (0.9, (0, 2)), (0.9, (0, 2))]


def test_non_pii_filter_keeps_a_span_with_a_nan_score(detector, monkeypatch) -> None:
    nan_span = _span(0, 4, "PERSON_NAME", float("nan"))
    monkeypatch.setattr(detector, "_non_pii_filter", _PassthroughFilter())

    assert detector._apply_non_pii_filter([nan_span], "abcd", "en") == [nan_span]


@pytest.mark.parametrize(
    ("cue", "value"),
    [
        ("Social security number:", "123-45-6789"),
        ("Passport no", "X1234567"),
        ("Tax ID", "123456789"),
    ],
)
def test_cue_and_value_swept_across_both_edges_equal_unwindowed(detector, cue, value) -> None:
    filler = " ".join(["abc"] * 2000)
    for edge in (1000, 1900):
        for position in range(edge - len(cue) - len(value) - 3, edge + 3):
            text = (filler[:position] + " " + cue + " " + value + " " + filler[position:])[:2600]
            assert _spans(detector, text, windowing=_SMALL) == _spans(detector, text), (
                cue,
                edge,
                position,
            )


def test_overlapping_candidates_resolve_after_the_merge(detector) -> None:
    filler = " ".join(["abc"] * 2000)
    body = "jane.doe@example.com or +44 20 7946 0958, IBAN GB82WEST12345698765432, 12345."
    for edge in (1000, 1900):
        for position in range(edge - len(body) - 2, edge + 3):
            text = (filler[:position] + " " + body + " " + filler[position:])[:2600]
            assert _spans(detector, text, windowing=_SMALL) == _spans(detector, text), (
                edge,
                position,
            )


def test_fragment_with_the_same_range_as_a_whole_span_is_dropped() -> None:
    merged = merge_window_spans(_W, [[_span(90, 100, "URL")], [_span(30, 40, "EMAIL_ADDRESS")]])

    assert [(s.start, s.end, s.entity_type) for s in merged] == [(90, 100, "EMAIL_ADDRESS")]


def test_fragment_outlasting_a_whole_span_by_one_char_is_kept() -> None:
    merged = merge_window_spans(_W, [[_span(90, 100)], [_span(30, 39, "EMAIL_ADDRESS")]])

    assert [(s.start, s.end) for s in merged] == [(90, 99), (90, 100)]


def test_fragment_is_kept_when_a_whole_span_starts_inside_it_and_extends_past_it() -> None:
    merged = merge_window_spans(_W, [[_span(90, 100)], [_span(35, 50, "EMAIL_ADDRESS")]])

    assert [(s.start, s.end) for s in merged] == [(90, 100), (95, 110)]


def test_fragment_is_dropped_by_a_long_whole_span_that_starts_before_a_shorter_one() -> None:
    merged = merge_window_spans(
        _W, [[_span(90, 100)], [_span(5, 45, "EMAIL_ADDRESS"), _span(10, 20, "PHONE_NUMBER")]]
    )

    assert [(s.start, s.end) for s in merged] == [(65, 105), (70, 80)]


@dataclass
class _NonPiiRule:
    pattern: str
    entity_types: list[str]
    pattern_type: str = "regex"
    languages: list[str] = field(default_factory=list)
    min_score: float | None = None
    is_active: bool = True


def test_non_pii_filter_rejection_after_the_merge_equals_unwindowed(detector, monkeypatch) -> None:
    rule = _NonPiiRule(pattern=r"^\+44", entity_types=["PHONE_NUMBER"])
    monkeypatch.setattr(detector, "_non_pii_filter", NonPiiFilter.from_rules([rule]))
    filler = " ".join(["abc"] * 2000)
    body = "jane.doe@example.com or +44 20 7946 0958, IBAN GB82WEST12345698765432"
    for edge in (1000, 1900):
        for position in range(edge - len(body) - 2, edge + 3):
            text = (filler[:position] + " " + body + " " + filler[position:])[:2600]
            windowed = _spans(detector, text, windowing=_SMALL)
            assert windowed == _spans(detector, text), (edge, position)
            assert sorted(kind for _, _, kind in windowed) == ["EMAIL_ADDRESS", "IBAN"]


@dataclass
class _Regulation:
    id: str
    entity_types: list[str]


@dataclass
class _CustomRegex:
    pattern: str
    id: int = 1
    name: str = "record"
    entity_type: str = "RECORD"
    detection_method: str = "regex"
    keywords: list[str] | None = None
    llm_prompt: str | None = None
    context_words: list[str] = field(default_factory=list)
    placeholder_label: str = "RECORD"
    is_active: bool = True


def _custom_regex_detector(pattern: str) -> Detector:
    policy = PolicyComposer().compose_from_data(
        [_Regulation(id="gdpr", entity_types=["EMAIL_ADDRESS"])], [_CustomRegex(pattern)], []
    )
    return Detector(config=_CONFIG, policy=policy, semantic_port=NullSemanticDetectionPort())


def _covered(detector: Detector, text: str, **kwargs) -> set[int]:
    covered: set[int] = set()
    for span in detector.detect(text, "en", **kwargs).spans:
        covered.update(range(span.start, span.end))
    return covered


def _no_window_holds(text: str, start: int, end: int) -> bool:
    return not any(w.start <= start and end <= w.end for w in plan_text_windows(text, _SMALL))


_RECORD = "RECORD[" + " ".join(["abc"] * 75) + "]"


@pytest.mark.parametrize("offset", [750, 820, 880])
def test_delimited_custom_match_longer_than_the_overlap_is_covered_across_a_cut(
    offset: int,
) -> None:
    detector = _custom_regex_detector(r"RECORD\[[^\]]*\]")
    filler = " ".join(["lorem"] * 600)
    text = filler[:offset] + " " + _RECORD + " " + filler[offset:]
    start = text.index(_RECORD)
    end = start + len(_RECORD)
    assert len(_RECORD) > _SMALL.overlap_chars
    assert _no_window_holds(text, start, end)

    assert set(range(start, end)) <= _covered(detector, text, windowing=_SMALL)
    assert _covered(detector, text, windowing=_SMALL) == _covered(detector, text)


@pytest.mark.parametrize("offset", [750, 820, 880])
def test_prefix_anchored_custom_match_is_covered_to_its_end_across_a_cut(offset: int) -> None:
    detector = _custom_regex_detector(r"RECORD\[[^\]\n]*")
    filler = " ".join(["lorem"] * 600)
    text = filler[:offset] + " " + _RECORD + " " + filler[offset:]
    start = text.index(_RECORD)
    end = start + len(_RECORD) - 1
    assert _no_window_holds(text, start, end)

    spans = [
        (s.start, s.end)
        for s in detector.detect(text, "en", windowing=_SMALL).spans
        if s.entity_type == "RECORD"
    ]
    assert spans == [(start, end)]


def test_long_structural_address_line_is_covered_across_a_cut(detector) -> None:
    line = " ".join(["lorem"] * 90) + " NO 5 APT 12"
    before = "\n".join(["ipsum dolor"] * 58) + "\n"
    text = before + line + "\n" + "\n".join(["ipsum dolor"] * 120)
    start = len(before)
    end = start + len(line)
    assert _no_window_holds(text, start, end)

    assert set(range(start, end)) <= _covered(detector, text, windowing=_SMALL)
    assert _covered(detector, text, windowing=_SMALL) == _covered(detector, text)


def test_spacy_never_sees_more_than_one_window(detector, monkeypatch) -> None:
    lengths: list[int] = []
    engine = detector._analyzer.nlp_engine
    real = engine.process_text

    def spy(text, language):
        lengths.append(len(text))
        return real(text, language)

    monkeypatch.setattr(engine, "process_text", spy)
    text = _document(random.Random(5), 6000)
    detector.detect(text, "en", windowing=_SMALL)

    assert lengths and max(lengths) <= _SMALL.window_chars


def test_merge_drops_a_full_text_span_a_window_saw_whole() -> None:
    reported = merge_window_spans(_W, [[_span(10, 20, score=0.9)], []], [_span(10, 20, score=0.5)])
    filtered = merge_window_spans(_W, [[], []], [_span(10, 20)])

    assert [(s.start, s.end, s.score) for s in reported] == [(10, 20, 0.9)]
    assert filtered == []


def test_merge_keeps_a_straddling_full_text_span_and_drops_its_fragments() -> None:
    merged = merge_window_spans(_W, [[_span(50, 100)], [_span(0, 20)]], [_span(50, 130)])

    assert [(s.start, s.end) for s in merged] == [(50, 130)]


def _unbroken(length: int) -> str:
    return ";".join(["lorem"] * (length // 6 + 1))[:length]


@pytest.mark.parametrize("value", ["jane.doe@example.com", "GB82WEST12345698765432"])
@pytest.mark.parametrize("shift", [12, 15, 18])
def test_pattern_entity_straddling_a_window_cut_is_masked_whole(detector, value, shift) -> None:
    text = _unbroken(2500)
    start = _NARROW.window_chars - shift
    text = text[: start - 1] + ";" + value + ";" + text[start + len(value) + 1 :]
    windows = plan_text_windows(text, _NARROW)
    assert any(start < w.end < start + len(value) for w in windows)
    assert not any(w.start <= start and start + len(value) <= w.end for w in windows)

    spans = detector.detect(text, "en", windowing=_NARROW).spans
    hits = [(s.start, s.end) for s in spans if s.start < start + len(value) and s.end > start]
    assert hits == [(start, start + len(value))]
    assert _spans(detector, text, windowing=_NARROW) == _spans(detector, text)


def test_pattern_entity_seen_whole_by_a_window_is_reported_once_with_the_window_score(
    detector,
) -> None:
    value = "jane.doe@example.com"
    text = "contact email: " + value + " " + _document(random.Random(11), 2500)
    start = text.index(value)
    assert any(
        w.start <= start and start + len(value) <= w.end
        for w in plan_text_windows(text, _SMALL)
    )

    windowed = detector.detect(text, "en", windowing=_SMALL).spans
    whole = detector.detect(text, "en").spans

    def pick(spans):
        return [(s.start, s.end, s.score) for s in spans if s.start == start]

    assert len(pick(windowed)) == 1
    assert pick(windowed) == pick(whole)


def test_pattern_pass_runs_no_spacy_recognizer_and_passes_no_nlp_artifacts(
    detector, monkeypatch
) -> None:
    recognizers = detector._analyzer.registry.get_recognizers(language="en", all_fields=True)
    assert any(isinstance(r, SpacyRecognizer) for r in recognizers)
    seen: list[object] = []

    def forbidden(*args, **kwargs):
        raise AssertionError("model layer ran in the full-text pass")

    monkeypatch.setattr(SpacyRecognizer, "analyze", forbidden)
    monkeypatch.setattr(detector._analyzer.nlp_engine, "process_text", forbidden)
    for recognizer in recognizers:
        if isinstance(recognizer, SpacyRecognizer):
            continue
        original = recognizer.analyze

        def spy(text, entities, nlp_artifacts=None, _o=original):
            seen.append(nlp_artifacts)
            return _o(text=text, entities=entities, nlp_artifacts=nlp_artifacts)

        monkeypatch.setattr(recognizer, "analyze", spy)

    spans = detector._pattern_spans("write to jane.doe@example.com today")

    assert seen and all(artifacts is None for artifacts in seen)
    assert any(s.entity_type == "EMAIL_ADDRESS" for s in spans)


def test_pattern_pass_skips_a_recognizer_outside_the_allow_list(
    detector, monkeypatch
) -> None:
    calls: list[str] = []

    class ModelBackedRecognizer(EntityRecognizer):
        def load(self) -> None:
            pass

        def analyze(self, text, entities, nlp_artifacts=None):
            calls.append(text)
            return []

    model = ModelBackedRecognizer(
        supported_entities=["EMAIL_ADDRESS"], name="model_backed", supported_language="en"
    )
    registry = detector._analyzer.registry
    monkeypatch.setattr(registry, "recognizers", [*registry.recognizers, model])
    selected = registry.get_recognizers(
        language="en", entities=detector._expand_with_aliases(detector._entity_types)
    )
    assert model in selected

    spans = detector._pattern_spans("write to jane.doe@example.com today")

    assert calls == []
    assert any(s.entity_type == "EMAIL_ADDRESS" for s in spans)


_MODEL_BACKED_RECOGNIZER_TYPES = (SpacyRecognizer,)


def test_every_registered_recognizer_is_full_text_safe_or_model_backed() -> None:
    engine = SeptumEngine(
        regulations=[str(reg_id) for reg_id in BUILTIN_REGULATION_IDS],
        config=_CONFIG,
        semantic_port=NullSemanticDetectionPort(),
    )
    registered = engine._detector._analyzer.registry.recognizers
    assert registered

    unclassified = sorted(
        {
            f"{type(r).__module__}.{type(r).__name__}"
            for r in registered
            if not isinstance(
                r, (*detector_module._FULL_TEXT_RECOGNIZER_TYPES, *_MODEL_BACKED_RECOGNIZER_TYPES)
            )
        }
    )

    assert not unclassified, (
        "recognizer classes outside _FULL_TEXT_RECOGNIZER_TYPES and the model-backed set "
        f"would silently skip the full-text pass: {unclassified}"
    )
