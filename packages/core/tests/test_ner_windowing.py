"""Token windowing for transformer NER: budget, offsets, seams and merge."""

from __future__ import annotations

import random

import pytest
from ner_fakes import NAME, FakePipeline, document
from septum_core import ner_model_registry as registry
from septum_core.ner_model_registry import _WindowedNERPipeline
from septum_core.ner_windowing import (
    NERWindowingError,
    Span,
    Window,
    _complete,
    _contained,
    _cut_ok,
    _token_ranges,
    _whole_index,
    merge_window_spans,
    plan_windows,
)

NAME_TEXT = " ".join(NAME)
VERY_LARGE = 10**30


def _found(spans: list[dict], text: str) -> list[tuple[int, int, str]]:
    return [(s["start"], s["end"], text[s["start"]:s["end"]]) for s in spans]


def _expected(text: str) -> list[tuple[int, int, str]]:
    at = text.index(NAME_TEXT)
    return [(at, at + len(NAME_TEXT), NAME_TEXT)]


def _unit_offsets(sizes: list[int]) -> tuple[str, list[tuple[int, int]]]:
    """One unit per size: ``size`` tokens sharing the unit's characters (as NFKC does)."""
    text = " ".join("c" for _ in sizes)
    offsets: list[tuple[int, int]] = []
    for unit, size in enumerate(sizes):
        start = 2 * unit
        offsets.extend([(start, start + 1)] * size)
    return text, offsets


def _interior_cuts(ranges: list[tuple[int, int]]) -> list[int]:
    return [t1 for _, t1 in ranges[:-1]] + [t0 for t0, _ in ranges[1:]]


def _counting_plan(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record the limit of every planning pass; the plans themselves are unchanged."""
    limits: list[int] = []
    real = registry.plan_windows

    def _counting(offsets, text_length, max_tokens, *args, **kwargs):
        limits.append(max_tokens)
        return real(offsets, text_length, max_tokens, *args, **kwargs)

    monkeypatch.setattr(registry, "plan_windows", _counting)
    return limits


_TWO = [
    Window(char_start=0, char_end=20, first=True, last=False),
    Window(char_start=10, char_end=30, first=False, last=True),
]


@pytest.mark.parametrize(
    "max_positions, model_max_length, padding_idx, expected",
    [
        (514, 512, None, 510),
        (514, 512, 1, 510),
        (2048, 512, 1, 510),
        (514, VERY_LARGE, None, 512),
        (514, VERY_LARGE, 1, 510),
        (514, VERY_LARGE, 0, 511),
        (514, 512, 0, 510),
    ],
)
def test_budget_is_the_smaller_of_the_model_limits_less_the_specials(
    max_positions: int, model_max_length: int, padding_idx: int | None, expected: int
) -> None:
    pipe = FakePipeline(
        max_positions=max_positions, model_max_length=model_max_length, padding_idx=padding_idx
    )

    assert _WindowedNERPipeline(pipe, None)._budget == expected


def test_sentinel_model_max_length_without_position_embeddings_fails_at_construction() -> None:
    pipe = FakePipeline(model_max_length=VERY_LARGE, embeddings=False)

    with pytest.raises(NERWindowingError, match="cannot derive"):
        _WindowedNERPipeline(pipe, None)


def test_a_budget_too_small_for_a_window_fails_at_construction() -> None:
    too_small = FakePipeline(max_positions=7, model_max_length=7, specials=2)
    with pytest.raises(NERWindowingError, match="too small"):
        _WindowedNERPipeline(too_small, None)

    just_enough = FakePipeline(max_positions=8, model_max_length=8, specials=2)
    assert _WindowedNERPipeline(just_enough, None)._budget == 6


def test_short_text_is_one_batched_call_with_unchanged_output() -> None:
    text, _ = document(8, name_at=[2])
    pipe = FakePipeline(max_positions=12, model_max_length=12)

    result = _WindowedNERPipeline(pipe, None)(text)

    assert pipe.chunks == [text]
    assert pipe.batch_sizes == [8]
    assert _found(result, text) == _expected(text)


def test_single_window_text_at_the_full_budget_is_not_truncated() -> None:
    text, _ = document(10, name_at=[8])
    pipe = FakePipeline(max_positions=12, model_max_length=12)

    result = _WindowedNERPipeline(pipe, None)(text)

    assert pipe.chunks == [text]
    assert pipe.truncated == 0
    assert _found(result, text) == _expected(text)


@pytest.mark.parametrize("count", [11, 17, 23, 40])
def test_every_name_position_is_found_once_with_exact_offsets(count: int) -> None:
    for start in range(count - 1):
        text, _ = document(count, name_at=[start])
        pipe = FakePipeline(max_positions=12, model_max_length=12)

        result = _WindowedNERPipeline(pipe, None)(text)

        assert _found(result, text) == _expected(text), start
        assert pipe.truncated == 0


@pytest.mark.parametrize("total", [511, 512, 960])
@pytest.mark.parametrize("drift", [0, 3])
def test_name_at_the_end_of_a_long_text_is_found_without_truncation(total: int, drift: int) -> None:
    text, _ = document(total, name_at=[total - 2])
    pipe = FakePipeline(max_positions=514, model_max_length=512, drift=drift)

    result = _WindowedNERPipeline(pipe, None)(text)

    assert _found(result, text) == _expected(text)
    assert pipe.truncated == 0
    assert all(len(c.split()) + drift <= 510 for c in pipe.chunks)


@pytest.mark.parametrize("start", range(500, 514))
@pytest.mark.parametrize("drift", [0, 3])
def test_name_across_the_first_window_seam_with_the_real_budget(start: int, drift: int) -> None:
    text, _ = document(1100, name_at=[start])
    pipe = FakePipeline(max_positions=514, model_max_length=512, drift=drift)

    result = _WindowedNERPipeline(pipe, None)(text)

    assert _found(result, text) == _expected(text)
    assert pipe.truncated == 0


def test_name_after_the_first_budget_is_detected() -> None:
    text, _ = document(700, name_at=[600])
    pipe = FakePipeline(max_positions=514, model_max_length=512)

    result = _WindowedNERPipeline(pipe, None)(text)

    assert _found(result, text) == _expected(text)
    assert len(pipe.chunks) == 2


def test_drift_within_the_margin_is_absorbed_in_one_planning_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limits = _counting_plan(monkeypatch)
    text, _ = document(60, name_at=[30])
    pipe = FakePipeline(max_positions=12, model_max_length=12, drift=4)

    result = _WindowedNERPipeline(pipe, None)(text)

    assert _found(result, text) == _expected(text)
    assert len(limits) == 1
    assert pipe.truncated == 0
    assert all(len(c.split()) <= 6 for c in pipe.chunks)


@pytest.mark.parametrize("drift", [5, 6])
def test_drift_beyond_the_margin_replans_with_shorter_windows(
    monkeypatch: pytest.MonkeyPatch, drift: int
) -> None:
    text, _ = document(60, name_at=[30])
    base = FakePipeline(max_positions=12, model_max_length=12, drift=0)
    _WindowedNERPipeline(base, None)(text)
    base_longest = max(len(c.split()) for c in base.chunks)
    limits = _counting_plan(monkeypatch)
    pipe = FakePipeline(max_positions=12, model_max_length=12, drift=drift)

    result = _WindowedNERPipeline(pipe, None)(text)

    assert _found(result, text) == _expected(text)
    assert pipe.truncated == 0
    assert len(limits) == 2
    assert max(len(c.split()) for c in pipe.chunks) < base_longest


def test_shrinking_to_a_limit_of_two_still_succeeds() -> None:
    text, _ = document(40, name_at=[20])
    pipe = FakePipeline(max_positions=12, model_max_length=12, drift=8)

    _WindowedNERPipeline(pipe, None)(text)

    assert pipe.truncated == 0
    assert max(len(c.split()) for c in pipe.chunks) == 2


def test_a_window_that_cannot_fit_even_after_shrinking_raises() -> None:
    text, _ = document(30)
    pipe = FakePipeline(max_positions=12, model_max_length=12, drift=20)

    with pytest.raises(NERWindowingError, match="cannot be fitted"):
        _WindowedNERPipeline(pipe, None)(text)

    assert pipe.batch_sizes == []


def test_a_window_cut_never_splits_tokens_that_share_characters() -> None:
    # The first word expands to six tokens sharing its characters, as NFKC does
    # to one compatibility character. A cut inside them would re-tokenize larger.
    text, _ = document(40, name_at=[20])
    text = "GROUP " + text
    pipe = FakePipeline(max_positions=12, model_max_length=12, groups={"GROUP": 6})

    result = _WindowedNERPipeline(pipe, None)(text)

    assert [t for _, _, t in _found(result, text)] == [NAME_TEXT]
    assert pipe.truncated == 0


def test_multi_window_text_is_scored_in_one_batch_of_eight() -> None:
    text, _ = document(960, name_at=[900])
    pipe = FakePipeline(max_positions=514, model_max_length=512)

    _WindowedNERPipeline(pipe, None)(text)

    assert pipe.batch_sizes == [8]


def test_label_map_renames_known_labels_and_drops_the_rest() -> None:
    text = "name game"
    pipe = FakePipeline(
        max_positions=12, model_max_length=12, emit={"name": "name", "game": "game"}
    )

    result = _WindowedNERPipeline(pipe, {"name": "PER"})(text)

    assert [(s["entity_group"], s["start"], s["end"]) for s in result] == [("PER", 0, 4)]


def test_empty_text_makes_no_call() -> None:
    pipe = FakePipeline(max_positions=12, model_max_length=12)

    assert _WindowedNERPipeline(pipe, None)("") == []
    assert pipe.chunks == []


@pytest.mark.parametrize("count", [1, 9, 10, 11, 25, 100, 1000])
@pytest.mark.parametrize("budget, overlap", [(10, 3), (10, 0), (506, 64), (2044, 64)])
def test_windows_cover_every_token_without_gaps(count: int, budget: int, overlap: int) -> None:
    text, offsets = document(count)

    windows = plan_windows(offsets, len(text), budget, overlap)

    assert windows[0].first and windows[-1].last
    assert windows[0].char_start == 0 and windows[-1].char_end == len(text)
    for prev, nxt in zip(windows, windows[1:]):
        assert prev.char_start < nxt.char_start
        assert text[prev.char_end:nxt.char_start].strip() == ""


def test_overlap_must_leave_progress() -> None:
    _, offsets = document(30)

    with pytest.raises(ValueError):
        plan_windows(offsets, 100, 10, 5)


def test_windowing_error_is_a_runtime_error() -> None:
    assert issubclass(NERWindowingError, RuntimeError)


def test_span_seen_only_by_the_second_window_is_kept() -> None:
    # (12, 18) lies whole in both windows, yet only window 1 reported it.
    merged = merge_window_spans(_TWO, [[], [Span(12, 18, "PER", 0.9)]], "A" * 30)

    assert merged == [Span(12, 18, "PER", 0.9)]


def test_full_name_reported_by_both_windows_is_one_span() -> None:
    merged = merge_window_spans(
        _TWO,
        [[Span(12, 18, "PER", 0.8)], [Span(12, 18, "PER", 0.9)]],
        "A" * 30,
    )

    assert merged == [Span(12, 18, "PER", 0.9)]


def test_entity_longer_than_the_overlap_survives_as_the_union_of_fragments() -> None:
    # The entity (8, 25) is cut by both windows: 8-20 from window 0 and 10-25
    # from window 1, neither whole.
    merged = merge_window_spans(
        _TWO,
        [[Span(8, 20, "PER", 0.9)], [Span(10, 25, "PER", 0.7)]],
        "A" * 30,
    )

    assert merged == [Span(8, 25, "PER", 0.9)]


def test_fragment_inside_a_whole_span_is_dropped() -> None:
    # Window 1 cut the name at its left edge (10, 14); window 0 has it whole.
    merged = merge_window_spans(
        _TWO,
        [[Span(8, 16, "PER", 0.9)], [Span(10, 14, "PER", 0.6)]],
        "A" * 30,
    )

    assert merged == [Span(8, 16, "PER", 0.9)]


def test_fragment_inside_a_different_label_whole_span_is_kept() -> None:
    merged = merge_window_spans(
        _TWO,
        [[Span(8, 16, "LOC", 0.9)], [Span(10, 14, "PER", 0.6)]],
        "A" * 30,
    )

    assert merged == [Span(8, 16, "LOC", 0.9), Span(10, 14, "PER", 0.6)]


def test_overlapping_spans_of_different_labels_stay_separate() -> None:
    merged = merge_window_spans(
        _TWO,
        [[Span(12, 18, "PER", 0.9)], [Span(14, 20, "LOC", 0.9)]],
        "A" * 30,
    )

    assert merged == [Span(12, 18, "PER", 0.9), Span(14, 20, "LOC", 0.9)]


def test_genuine_seam_split_name_is_joined() -> None:
    # "Ada" (9, 12) and "Lovelace" (13, 21) are separated by one space inside
    # the overlap zone [10, 20], so they are one name cut by the seam.
    text = "A" * 9 + "Ada Lovelace" + "A" * 9

    merged = merge_window_spans(
        _TWO,
        [[Span(9, 12, "PER", 0.9)], [Span(13, 21, "PER", 0.8)]],
        text,
    )

    assert merged == [Span(9, 21, "PER", 0.9)]


def test_newline_separated_names_crossing_a_seam_stay_separate() -> None:
    text = "Ada\nBob\nCarl\nDana" + "A" * 11
    spans = [
        Span(0, 3, "PER", 0.9),
        Span(4, 7, "PER", 0.9),
        Span(8, 12, "PER", 0.9),
        Span(13, 17, "PER", 0.9),
    ]

    merged = merge_window_spans(_TWO, [spans, []], text)

    assert merged == spans


def test_merge_does_not_join_split_spans_away_from_a_seam() -> None:
    text = "Ada Lo" + "A" * 24

    merged = merge_window_spans(_TWO, [[Span(0, 3, "PER", 0.9), Span(4, 6, "PER", 0.8)], []], text)

    assert merged == [Span(0, 3, "PER", 0.9), Span(4, 6, "PER", 0.8)]


@pytest.mark.parametrize("gap, joined", [
    (" ", True),
    (" ", True),
    ("　", True),
    ("  ", True),
    ("", True),
    ("\t", False),
    ("\r", False),
    ("\n", False),
    ("\v", False),
    ("\f", False),
    (" ", False),
    (" ", False),
    ("\u0085", False),
    ("​", False),
    (",", False),
    (" ,", False),
])
def test_only_intra_line_spaces_join_a_seam_split_name(gap: str, joined: bool) -> None:
    # "Ada" (9-12) and "Lovelace" begin 12+len(gap) and end 20+len(gap): the
    # gap sits inside the overlap zone [10, 20] for these lengths.
    text = "A" * 9 + "Ada" + gap + "Lovelace" + "A" * 10
    prev = Span(9, 12, "PER", 0.9)
    nxt = Span(12 + len(gap), 20 + len(gap), "PER", 0.8)

    merged = merge_window_spans(_TWO, [[prev], [nxt]], text)

    if joined:
        assert merged == [Span(9, 20 + len(gap), "PER", 0.9)]
    else:
        assert merged == [prev, nxt]


def test_gap_starting_inside_a_zone_and_ending_after_it_does_not_join() -> None:
    # The gap [18, 21) starts inside the zone [10, 20] and ends after it.
    text = "A" * 15 + "Ada" + "   " + "Lovelac" + "e" + "A" * 4
    prev = Span(15, 18, "PER", 0.9)
    nxt = Span(21, 29, "PER", 0.8)

    assert merge_window_spans(_TWO, [[prev], [nxt]], text) == [prev, nxt]


def test_gap_exactly_on_the_zone_boundaries_joins() -> None:
    # The gap [10, 20) is exactly the zone: both ends are inclusive.
    text = "A" * 10 + " " * 10 + "B" * 10
    prev = Span(0, 10, "PER", 0.9)
    nxt = Span(20, 25, "PER", 0.8)

    assert merge_window_spans(_TWO, [[prev], [nxt]], text) == [Span(0, 25, "PER", 0.9)]


def test_name_split_at_the_second_of_two_seams_is_joined() -> None:
    windows = [
        Window(char_start=0, char_end=20, first=True, last=False),
        Window(char_start=10, char_end=30, first=False, last=False),
        Window(char_start=20, char_end=40, first=False, last=True),
    ]
    text = "A" * 27 + " " + "B" * 12

    merged = merge_window_spans(
        windows,
        [[], [Span(24, 27, "PER", 0.9)], [Span(28, 33, "PER", 0.8)]],
        text,
    )

    assert merged == [Span(24, 33, "PER", 0.9)]


def test_single_window_merge_returns_its_spans_unchanged() -> None:
    windows = plan_windows([(0, 3)], 3, 10, 2)
    in_order = [Span(2, 3, "LOC", 0.9), Span(0, 1, "PER", 0.9)]
    overlapping = [Span(0, 2, "PER", 0.9), Span(1, 3, "PER", 0.8)]

    assert merge_window_spans(windows, [in_order], "abc") == in_order
    assert merge_window_spans(windows, [overlapping], "abc") == overlapping


def _naive_merge(windows, per_window, text):
    if len(windows) == 1:
        return list(per_window[0])
    whole = [s for k, sp in enumerate(per_window) for s in sp if _complete(s, windows[k])]
    fragments = [
        s
        for k, sp in enumerate(per_window)
        for s in sp
        if not _complete(s, windows[k])
        and not any(w.label == s.label and w.start <= s.start and s.end <= w.end for w in whole)
    ]
    kept = sorted(set(whole + fragments), key=lambda s: (s.start, s.end, s.label))
    seams = [(windows[k + 1].char_start, windows[k].char_end) for k in range(len(windows) - 1)]
    out: list[Span] = []
    for span in kept:
        if out:
            prev = out[-1]
            gap = text[prev.end:span.start]
            gap_ok = all(c in "  　" for c in gap) and any(
                a <= prev.end and span.start <= b for a, b in seams
            )
            if prev.label == span.label and (span.start < prev.end or gap_ok):
                out[-1] = Span(prev.start, max(prev.end, span.end), prev.label, max(prev.score, span.score))
                continue
        out.append(span)
    return out


@pytest.mark.parametrize("seed", range(200))
def test_merge_matches_a_naive_reference_on_random_inputs(seed: int) -> None:
    rng = random.Random(seed)
    count = rng.randint(40, 200)
    text = "".join(rng.choice("ab  \t\n") for _ in range(count))
    windows = plan_windows([(i, i + 1) for i in range(count)], count, rng.randint(8, 30))
    per_window = []
    for window in windows:
        spans = []
        for _ in range(rng.randint(0, 8)):
            a = rng.randint(window.char_start, window.char_end - 1)
            b = rng.randint(a + 1, min(window.char_end, a + 12))
            spans.append(Span(a, b, rng.choice("PL"), round(rng.random(), 2)))
        per_window.append(spans)

    assert merge_window_spans(windows, per_window, text) == _naive_merge(windows, per_window, text)


def test_fragment_containment_uses_the_running_maximum_end() -> None:
    index = _whole_index([Span(0, 100, "PER", 0.9), Span(10, 20, "PER", 0.9)])

    assert _contained(Span(30, 40, "PER", 0.5), index)
    assert _contained(Span(0, 100, "PER", 0.5), index)
    assert not _contained(Span(105, 110, "PER", 0.5), index)
    assert not _contained(Span(30, 40, "LOC", 0.5), index)


def test_fragment_containment_boundaries() -> None:
    from_five = _whole_index([Span(5, 20, "PER", 0.9)])
    from_zero = _whole_index([Span(0, 20, "PER", 0.9)])

    assert not _contained(Span(0, 3, "PER", 0.5), from_five)
    assert _contained(Span(5, 10, "PER", 0.5), from_five)
    assert not _contained(Span(15, 21, "PER", 0.5), from_zero)


def test_limit_of_three_plans_without_an_argument_error() -> None:
    text, offsets = document(12)

    windows = plan_windows(offsets, len(text), 3)

    assert windows[0].first and windows[-1].last
    assert max(w.char_end for w in windows) == len(text)


def test_repeated_cut_positions_from_shared_offset_groups_still_cover_the_text() -> None:
    text, offsets = _unit_offsets([3, 1, 1, 5] + [1] * 6)

    windows = plan_windows(offsets, len(text), 6)

    assert windows[0].char_start == 0 and windows[-1].char_end == len(text)
    assert all(prev.char_start < nxt.char_start for prev, nxt in zip(windows, windows[1:]))


def test_first_and_last_windows_reach_the_text_edges() -> None:
    body, offsets = document(40, name_at=[20])
    text = "   " + body + "  "
    shifted = [(a + 3, b + 3) for a, b in offsets]

    windows = plan_windows(shifted, len(text), 10)

    assert windows[0].char_start == 0
    assert windows[-1].char_end == len(text)


def test_no_planned_cut_falls_inside_a_shared_offset_group_at_the_first_cut() -> None:
    # Units 0-7 are one token each, unit 8 is six tokens (indices 8-13). The
    # first cut at token 10 lies inside it and must move back to token 8.
    _, offsets = _unit_offsets([1] * 8 + [6] + [1] * 10)

    ranges = _token_ranges(offsets, 10, 3)

    assert all(_cut_ok(offsets, t) for t in _interior_cuts(ranges))
    assert ranges[0][1] == 8


def test_no_planned_cut_falls_inside_a_group_within_the_overlap_region() -> None:
    # The second window would start at token 7, inside the four-token group
    # (indices 6-9). No cut fits in the overlap, so the next window starts at
    # the first token after the group.
    _, offsets = _unit_offsets([1] * 6 + [4] + [1] * 10)

    ranges = _token_ranges(offsets, 10, 3)

    assert all(_cut_ok(offsets, t) for t in _interior_cuts(ranges))
    assert ranges[1][0] == 10


def test_plan_windows_never_places_a_window_edge_between_group_tokens() -> None:
    text, offsets = _unit_offsets([1] * 8 + [6] + [1] * 10)

    windows = plan_windows(offsets, len(text), 10, 3)

    group_start, group_end = offsets[8]
    for window in windows:
        assert window.char_start <= group_start or window.char_start >= group_end
        assert window.char_end <= group_start or window.char_end >= group_end
    assert windows[0].char_end == offsets[7][1]


def test_group_wider_than_the_window_raises_the_windowing_error() -> None:
    _, offsets = _unit_offsets([6] + [1] * 10)

    with pytest.raises(NERWindowingError):
        plan_windows(offsets, 30, 4, 1)


def test_group_wider_than_the_budget_raises_the_windowing_error_through_the_wrapper() -> None:
    text, _ = _unit_offsets([12] + [1] * 12)
    pipe = FakePipeline(max_positions=12, model_max_length=12, groups={"c": 12})

    with pytest.raises(NERWindowingError, match="no token boundary"):
        _WindowedNERPipeline(pipe, None)(text)

    assert pipe.batch_sizes == []


def test_shared_offset_groups_fit_the_budget_without_truncation() -> None:
    # Five words of shared-offset groups plus one more: the plan at limit 6
    # leaves a window too long for the budget, and the shrunken plan fits.
    text = "a b c d e"
    pipe = FakePipeline(
        max_positions=12,
        model_max_length=12,
        groups={"a": 3, "b": 1, "c": 1, "d": 5, "e": 1},
    )

    _WindowedNERPipeline(pipe, None)(text)

    assert pipe.chunks
    assert pipe.truncated == 0
    assert all(len(pipe.tokenizer.pieces(c)) <= 10 for c in pipe.chunks)


def test_geometric_shrink_succeeds_where_a_linear_shrink_would_exceed_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Chunks longer than 400 tokens overflow by one. Linear shrinking would need
    # about 106 passes (506 down to 400); the geometric shrink needs 8.
    limits = _counting_plan(monkeypatch)
    counted: list[int] = []

    def _token_counts(self, chunks: list[str]) -> list[int]:
        counted.extend([1] * len(chunks))
        return [511 if len(c.split()) > 400 else len(c.split()) for c in chunks]

    monkeypatch.setattr(_WindowedNERPipeline, "_token_counts", _token_counts)
    text, _ = document(960, name_at=[900])
    pipe = FakePipeline(max_positions=514, model_max_length=512)

    result = _WindowedNERPipeline(pipe, None)(text)

    assert _found(result, text) == _expected(text)
    assert len(limits) == 8 <= registry._MAX_FIT_PASSES
    assert len(counted) <= 40
    assert all(limit >= 2 for limit in limits)


def test_an_always_overflowing_document_stops_at_the_pass_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    limits = _counting_plan(monkeypatch)
    monkeypatch.setattr(
        _WindowedNERPipeline, "_token_counts", lambda self, chunks: [100001] * len(chunks)
    )
    text, _ = document(100001)
    pipe = FakePipeline(max_positions=100002, model_max_length=100002, specials=2)

    with pytest.raises(NERWindowingError, match="cannot be fitted"):
        _WindowedNERPipeline(pipe, None)(text)

    assert len(limits) == registry._MAX_FIT_PASSES == 16
    assert all(limit >= 2 for limit in limits)


def test_a_window_exactly_at_the_budget_is_accepted_in_one_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    limits = _counting_plan(monkeypatch)
    monkeypatch.setattr(_WindowedNERPipeline, "_token_counts", lambda self, chunks: [10] * len(chunks))
    text, _ = document(960)
    pipe = FakePipeline(max_positions=12, model_max_length=12)

    _WindowedNERPipeline(pipe, None)(text)

    assert len(limits) == 1
    assert pipe.batch_sizes == [8]


def test_driving_the_limit_to_one_raises_the_windowing_error_not_a_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limits = _counting_plan(monkeypatch)
    monkeypatch.setattr(
        _WindowedNERPipeline,
        "_token_counts",
        lambda self, chunks: [10 + len(c.split()) - 1 for c in chunks],
    )
    text, _ = document(60)
    pipe = FakePipeline(max_positions=12, model_max_length=12)

    with pytest.raises(NERWindowingError) as info:
        _WindowedNERPipeline(pipe, None)(text)

    assert not isinstance(info.value, ValueError)
    assert limits == [6]


def test_a_non_windowing_error_from_planning_propagates_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(registry, "plan_windows", _boom)
    text, _ = document(960)
    pipe = FakePipeline(max_positions=12, model_max_length=12)

    with pytest.raises(ValueError, match="boom"):
        _WindowedNERPipeline(pipe, None)(text)


def test_a_pipeline_returning_fewer_result_lists_than_windows_raises_with_counts() -> None:
    class _Short(FakePipeline):
        def __call__(self, chunks, batch_size=None):
            return super().__call__(chunks, batch_size)[:-1]

    text, _ = document(60, name_at=[30])
    pipe = _Short(max_positions=12, model_max_length=12)

    with pytest.raises(NERWindowingError, match=r"pipeline returned \d+ result lists for \d+ windows"):
        _WindowedNERPipeline(pipe, None)(text)
