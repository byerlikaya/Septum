"""Per-block language segmentation with an injected (fake) detector."""

from __future__ import annotations

import random

import pytest
from septum_core.language_resolution import _SCRIPT_RULES, deciding_script
from septum_core.language_segments import (
    CONFIDENT_MIN_RUN_CHARS_BY_SCRIPT,
    DEFAULT_CONFIDENT_MIN_RUN_CHARS,
    DEFAULT_MIN_RUN_CHARS,
    MIN_DETECTION_LETTERS,
    MIN_RUN_CHARS_BY_SCRIPT,
    Segment,
    segment_languages,
)

TR = "Bu sözleşme iki taraf arasında ve karşılıklı olarak imzalanmıştır. " * 12
EN = "This agreement is signed between the two parties and applies to both. " * 12


def fake_detector(sample):
    """Marker-based stand-in: Turkish diacritics or ' ve ' -> tr, ' the ' -> en."""
    if " the " in sample:
        return [("en", 0.99)]
    if "ı" in sample or " ve " in sample:
        return [("tr", 0.99)]
    return [("en", 0.4), ("tr", 0.3)]


def build(*blocks):
    text = "".join(blocks)
    offsets, pos = [], 0
    for block in blocks[:-1]:
        pos += len(block)
        offsets.append(pos)
    return text, offsets


def run(text, boundaries, dominant="tr", detector=fake_detector):
    return segment_languages(
        text, boundaries, dominant_language=dominant, detect_candidates=detector
    )


def assert_covers(segments, text):
    assert segments[0].start == 0
    assert segments[-1].end == len(text)
    for left, right in zip(segments, segments[1:]):
        assert left.end == right.start
        assert left.language != right.language
    assert all(s.start < s.end for s in segments)


def test_single_language_is_one_segment():
    text, bounds = build(TR, TR, TR)
    assert run(text, bounds) == (Segment(0, len(text), "tr"),)


def test_annex_in_other_language_splits_at_block_edge():
    text, bounds = build(TR, TR, EN, EN)
    assert run(text, bounds) == (
        Segment(0, len(TR) * 2, "tr"),
        Segment(len(TR) * 2, len(text), "en"),
    )


def test_short_minority_block_inherits_context():
    quote = "the cat sat. " * 3
    assert len(quote) < DEFAULT_MIN_RUN_CHARS
    text, bounds = build(TR, quote, TR)
    assert run(text, bounds) == (Segment(0, len(text), "tr"),)


def test_digit_only_block_inherits_previous_language():
    digits = "1234 5678 9012\n" * 40
    assert len(digits) > DEFAULT_MIN_RUN_CHARS
    text, bounds = build(EN, digits, EN)
    assert run(text, bounds, dominant="tr") == (Segment(0, len(text), "en"),)


def test_leading_digit_block_takes_next_language():
    digits = "1234 5678 9012\n" * 40
    text, bounds = build(digits, EN)
    assert run(text, bounds, dominant="tr") == (Segment(0, len(text), "en"),)


def test_low_confidence_block_uses_dominant_context():
    vague = "lorem ipsum dolor sit amet consectetur " * 20
    text, bounds = build(TR, vague, TR)
    assert run(text, bounds) == (Segment(0, len(text), "tr"),)


def test_short_run_absorbed_by_longer_neighbour():
    small = "the end of the line is here. " * 5
    assert len(small) < DEFAULT_MIN_RUN_CHARS
    text, bounds = build(TR, small, EN, EN, EN)
    assert run(text, bounds) == (
        Segment(0, len(TR), "tr"),
        Segment(len(TR), len(text), "en"),
    )


def test_tiny_blocks_are_coalesced_into_few_detector_calls():
    calls = []

    def counting(sample):
        calls.append(len(sample))
        return [("tr", 0.99)]

    text, bounds = build(*["ve bu\n"] * 2000)
    assert run(text, bounds, detector=counting) == (Segment(0, len(text), "tr"),)
    assert len(calls) <= len(text) // DEFAULT_MIN_RUN_CHARS + 1


def test_detector_reads_bounded_sample():
    seen = []

    def spy(sample):
        seen.append(len(sample))
        return [("en", 0.99)]

    run(EN * 400, [], dominant="en", detector=spy)
    assert max(seen) <= 10_000


@pytest.mark.parametrize("bounds", [[], [0], [-5], [10**9], [0, 10**9]])
def test_boundaries_outside_text_are_ignored(bounds):
    assert run(TR, bounds) == (Segment(0, len(TR), "tr"),)


def test_boundary_convention_matches_detector_hints():
    text, bounds = build(TR, EN)
    noisy = [bounds[0], bounds[0], 0, len(text)]
    assert run(text, noisy) == run(text, bounds)


def test_empty_text_has_no_segments():
    assert run("", []) == ()


def test_short_whole_document_takes_dominant_language():
    assert run("the cat", []) == (Segment(0, 7, "tr"),)


def test_invalid_dominant_language_raises():
    with pytest.raises(ValueError):
        run(TR, [], dominant="not a code!")


def test_deterministic():
    text, bounds = build(TR, EN, TR, EN)
    assert run(text, bounds) == run(text, bounds)


@pytest.mark.parametrize("seed", range(25))
def test_coverage_and_contiguity_over_random_blocks(seed):
    rng = random.Random(seed)
    pool = [TR, EN, "12 34 56 ", "the end. ", "ve bu ", "x"]
    blocks = [rng.choice(pool) * rng.randint(1, 8) for _ in range(rng.randint(1, 30))]
    text, bounds = build(*blocks)
    assert_covers(run(text, bounds, dominant=rng.choice(["tr", "en"])), text)


DENSE_SAMPLES = {
    "Han": "温控器使用说明安全须知请先切断电源",
    "Kana": "これはぶんしょうのしけんようのてすとです",
    "Hangul": "이문서는시험용으로만들어진짧은글입니다",
    "Thai": "เอกสารนี้ใช้สำหรับทดสอบการแบ่งภาษา",
}
LANGUAGE_BY_SCRIPT = {"Han": "zh", "Kana": "ja", "Hangul": "ko", "Thai": "th", "Latin": "en"}


def by_script_detector(sample):
    return [(LANGUAGE_BY_SCRIPT[deciding_script(sample)], 0.99)]


def dense_block(script, length):
    sample = DENSE_SAMPLES[script]
    return (sample * (length // len(sample) + 1))[:length]


def test_min_run_table_is_keyed_by_known_scripts_and_covers_unspaced_ones():
    assert set(MIN_RUN_CHARS_BY_SCRIPT) <= set(_SCRIPT_RULES)
    assert set(MIN_RUN_CHARS_BY_SCRIPT) <= set(DENSE_SAMPLES)
    unspaced = {name for name, rule in _SCRIPT_RULES.items() if not rule.inter_word_spaces}
    assert unspaced <= set(MIN_RUN_CHARS_BY_SCRIPT)
    assert all(0 < floor < DEFAULT_MIN_RUN_CHARS for floor in MIN_RUN_CHARS_BY_SCRIPT.values())


@pytest.mark.parametrize("script", sorted(MIN_RUN_CHARS_BY_SCRIPT))
def test_dense_script_run_above_its_floor_stays_its_own_segment(script):
    floor = MIN_RUN_CHARS_BY_SCRIPT[script]
    block = dense_block(script, floor + 10)
    assert floor + 10 < DEFAULT_MIN_RUN_CHARS
    text, bounds = build(EN, block, EN)
    assert run(text, bounds, dominant="en", detector=by_script_detector) == (
        Segment(0, len(EN), "en"),
        Segment(len(EN), len(EN) + len(block), LANGUAGE_BY_SCRIPT[script]),
        Segment(len(EN) + len(block), len(text), "en"),
    )


@pytest.mark.parametrize("script", sorted(MIN_RUN_CHARS_BY_SCRIPT))
@pytest.mark.parametrize(
    ("delta", "own"),
    [(-10, False), (10, True)],
)
def test_dense_script_run_is_kept_only_above_its_confident_floor(script, delta, own):
    """These scripts settle their own language, so the confident (smaller) floor applies."""
    block = dense_block(script, CONFIDENT_MIN_RUN_CHARS_BY_SCRIPT[script] + delta)
    text, bounds = build(EN, block, EN)

    languages = [s.language for s in run(text, bounds, dominant="en", detector=by_script_detector)]

    assert languages == (["en", LANGUAGE_BY_SCRIPT[script], "en"] if own else ["en"])


def weak_detector(sample):
    """Right language, but never confident enough."""
    return [(code, min(prob, 0.9)) for code, prob in fake_detector(sample)] + [("xx", 0.2)]


@pytest.mark.parametrize(
    ("detector", "length", "own"),
    [
        (weak_detector, DEFAULT_MIN_RUN_CHARS - 50, False),
        (weak_detector, DEFAULT_MIN_RUN_CHARS + 50, True),
        (fake_detector, DEFAULT_CONFIDENT_MIN_RUN_CHARS - 20, False),
        (fake_detector, DEFAULT_CONFIDENT_MIN_RUN_CHARS + 20, True),
    ],
)
def test_spaced_script_run_floor_depends_on_detector_confidence(detector, length, own):
    block = TR[:length]
    text, bounds = build(EN, block, EN)

    languages = [s.language for s in run(text, bounds, dominant="en", detector=detector)]

    assert languages == (["en", "tr", "en"] if own else ["en"])


def test_a_confident_run_in_the_dominant_language_keeps_the_full_floor():
    """The lower floor is for a minority; a short dominant run between minority runs is still absorbed."""
    short_en = EN[: DEFAULT_CONFIDENT_MIN_RUN_CHARS + 20]
    text, bounds = build(TR, short_en, TR)

    assert [s.language for s in run(text, bounds, dominant="en")] == ["tr"]


def test_short_blocks_coalesced_into_one_unconfident_unit_keep_the_full_floor():
    """The two short minority blocks are judged as one unit (the second detector call), which is weak."""
    calls = []

    def mixed(sample):
        calls.append(sample)
        return weak_detector(sample) if len(calls) == 2 else fake_detector(sample)

    block_a = TR[:150] + "\n"
    block_b = TR[:150] + "\n"
    text, bounds = build(EN, block_a, block_b, EN)

    assert [s.language for s in run(text, bounds, dominant="en", detector=mixed)] == ["en"]


def test_run_floor_follows_the_script_deciding_its_own_text():
    mixed = dense_block("Han", 130) + " " + "ab12 " * 8
    assert deciding_script(mixed) == "Han"
    text, bounds = build(EN, mixed, EN)
    assert [s.language for s in run(text, bounds, dominant="en", detector=by_script_detector)] == [
        "en", "zh", "en",
    ]


def test_dense_script_document_in_its_own_language_is_one_segment():
    block = dense_block("Han", 60)
    text, bounds = build(block, block, block)
    assert run(text, bounds, dominant="zh", detector=by_script_detector) == (
        Segment(0, len(text), "zh"),
    )


def _naive_absorb(runs, dominant):
    """The shortest-first merge written the obvious quadratic way, as the reference."""
    from septum_core.language_resolution import deciding_script_from_counts

    def floor(run):
        return MIN_RUN_CHARS_BY_SCRIPT.get(
            deciding_script_from_counts(run[3], run[4]), DEFAULT_MIN_RUN_CHARS
        )

    while True:
        short = [i for i, r in enumerate(runs) if r[1] - r[0] < floor(r)]
        if not short:
            return [(r[0], r[1], r[2]) for r in runs]
        i = min(short, key=lambda k: (runs[k][1] - runs[k][0], k))
        if len(runs) == 1:
            return [(runs[0][0], runs[0][1], dominant)]
        prev_len = runs[i - 1][1] - runs[i - 1][0] if i > 0 else -1
        next_len = runs[i + 1][1] - runs[i + 1][0] if i + 1 < len(runs) else -1
        target = i - 1 if prev_len >= next_len else i + 1
        low, high = sorted((target, i))
        counts = {k: runs[low][3][k] + runs[high][3][k] for k in runs[low][3]}
        runs[low] = [runs[low][0], runs[high][1], runs[target][2], counts, runs[low][4] + runs[high][4]]
        del runs[high]


@pytest.mark.parametrize("seed", range(40))
def test_heap_absorption_matches_the_quadratic_reference(seed):
    from septum_core.language_segments import _absorb_short_runs, _Run

    rng = random.Random(seed)
    spec, position = [], 0
    for _ in range(rng.randint(1, 60)):
        length = rng.choice([30, 90, 130, 200, 350, 420, 900])
        script = rng.choice(["Latin", "Han", "Cyrillic"])
        language = rng.choice(["en", "tr", "zh", "ru"])
        counts = dict.fromkeys(_SCRIPT_RULES, 0)
        counts[script] = length
        spec.append((position, position + length, language, counts, length))
        position += length
    runs = [_Run(a, b, lang, dict(c), n) for a, b, lang, c, n in spec]
    reference = _naive_absorb([[a, b, lang, dict(c), n] for a, b, lang, c, n in spec], "en")

    result = [(r.start, r.end, r.language) for r in _absorb_short_runs(runs, "en")]

    assert result == reference


def test_many_alternating_blocks_segment_in_near_linear_time():
    import time

    blocks = [TR[:130] if i % 2 else EN[:130] for i in range(3000)]
    text, bounds = build(*blocks)
    started = time.perf_counter()
    segments = run(text, bounds, dominant="en")
    elapsed = time.perf_counter() - started

    assert segments[0].start == 0 and segments[-1].end == len(text)
    assert elapsed < 5.0


def han_or_en_detector(sample):
    if any("一" <= ch <= "鿿" for ch in sample):
        return [("zh-cn", 0.99)]
    if " the " in sample:
        return [("en", 0.99)]
    return []


def test_run_floor_comes_from_the_summed_letters_of_its_units():
    """A run's deciding script is the one with most letters over all its units' samples."""
    han = dense_block("Han", 100)
    latin = "alpha beta gamma delta " * 7
    latin_heavy = build(EN, latin, han, EN)
    han_heavy = build(EN, dense_block("Han", 200), latin[:100], EN)

    absorbed = run(*latin_heavy, dominant="zh", detector=han_or_en_detector)
    kept = run(*han_heavy, dominant="zh", detector=han_or_en_detector)

    assert [s.language for s in absorbed] == ["en"]
    assert [s.language for s in kept] == ["en", "zh", "en"]


def test_rebase_cost_stays_bounded_inside_a_long_run_of_combining_marks():
    import time
    import unicodedata

    from septum_core.language_segments import rebase_segments

    for size in (10_000, 50_000):
        text = "a" + "̖́" * (size // 2)
        step = len(text) // 128
        cuts = [i * step for i in range(128)] + [len(text)]
        segments = [Segment(a, b, "tr" if i % 2 else "en") for i, (a, b) in enumerate(zip(cuts, cuts[1:]))]
        started = time.perf_counter()
        rebased = rebase_segments(segments, text)
        elapsed = time.perf_counter() - started

        normalized = unicodedata.normalize("NFC", text)
        assert elapsed < 2.0
        if rebased is None:
            continue
        assert rebased[0].start == 0 and rebased[-1].end == len(normalized)
        assert all(a.end == b.start for a, b in zip(rebased, rebased[1:]))
        assert all(s.start < s.end for s in rebased)


def test_short_blocks_coalesce_up_to_the_default_minimum_run_size():
    calls = []

    def counting(sample):
        calls.append(len(sample))
        return [("tr", 0.99)]

    blocks = ["Bu sözleşme iki taraf arasında ve karşılıklı imzalanmıştır.\n"] * 60
    text, bounds = build(*blocks)
    assert len(blocks[0]) < DEFAULT_MIN_RUN_CHARS // 4

    assert run(text, bounds, detector=counting) == (Segment(0, len(text), "tr"),)
    assert len(calls) <= len(text) // DEFAULT_MIN_RUN_CHARS + 1
    assert min(calls[:-1]) >= DEFAULT_MIN_RUN_CHARS - len(blocks[0])


@pytest.mark.parametrize("script", sorted(MIN_RUN_CHARS_BY_SCRIPT))
def test_short_blocks_of_a_dense_script_coalesce_to_its_own_smaller_size(script):
    from septum_core.language_segments import _units

    floor = MIN_RUN_CHARS_BY_SCRIPT[script]
    text, bounds = build(*[dense_block(script, 25) + "\n"] * 60)

    units = _units(text, bounds)

    assert len(units) <= len(text) // floor + 1
    assert len(units) > len(text) // DEFAULT_MIN_RUN_CHARS


def test_a_short_block_is_never_joined_to_the_long_block_after_it():
    seen = []

    def spy(sample):
        seen.append(sample)
        return fake_detector(sample)

    short = ("Bu sözleşme ve ek.\n") * 3
    text, bounds = build(short, short, EN, EN)

    run(text, bounds, dominant="tr", detector=spy)

    assert any("karşı" not in sample and "the" not in sample for sample in seen[:1])
    assert not any("ek." in sample and " the " in sample for sample in seen)


def test_a_document_in_one_language_is_one_segment_whatever_the_block_sizes():
    sizes = [5, 80, 130, 250, 399, 400, 401, 900]
    blocks = [(TR * 4)[:size] + "\n" for size in sizes] * 3
    text, bounds = build(*blocks)
    assert run(text, bounds, dominant="tr") == (Segment(0, len(text), "tr"),)


def test_units_whose_scripts_allow_one_language_never_reach_the_detector():
    calls = []

    def counting(sample):
        calls.append(sample)
        return [("en", 0.99)]

    han = dense_block("Han", 300)
    text, bounds = build(EN, han, EN, dense_block("Hangul", 200), EN)

    segments = run(text, bounds, dominant="en", detector=counting)

    assert [s.language for s in segments] == ["en", "zh", "en", "ko", "en"]
    assert len(calls) == 3
    assert all(" the " in sample for sample in calls)


def test_a_document_of_one_dense_script_makes_no_detector_call():
    calls = []
    text, bounds = build(*[dense_block("Han", 150) + "\n"] * 40)

    segments = run(text, bounds, dominant="zh", detector=lambda s: calls.append(s) or [])

    assert calls == []
    assert segments == (Segment(0, len(text), "zh"),)


MARKED = {
    "weaktr": [("tr", 0.9)],
    "suretr": [("tr", 0.99)],
    "surede": [("de", 0.99)],
    "sureen": [("en", 0.99)],
}


def marker_detector(sample):
    return next(candidates for marker, candidates in MARKED.items() if marker in sample)


def marked(marker, length):
    return (f"{marker} lorem ipsum dolor. " * (length // 10 + 1))[:length]


@pytest.fixture
def block_units(monkeypatch):
    """Every block is a unit of its own, so a run can be made of short units.

    The public path cannot build such a run today: consecutive short blocks
    coalesce into one unit, and a short unit is flushed only by a long block
    next to it, so two short units of one language are never neighbours. The
    confidence rules on absorption and on joining same-language units are
    therefore guards ``segment_languages`` does not reach yet; this fixture
    pins them for the day unit building changes.
    """
    import septum_core.language_segments as module
    from septum_core.language_resolution import detection_sample, script_letter_counts

    def units(text, boundaries):
        edges = [0, *sorted({b for b in boundaries if 0 < b < len(text)}), len(text)]
        out = []
        for start, end in zip(edges, edges[1:]):
            sample = detection_sample(text[start:end])
            out.append((start, end, sample, *script_letter_counts(sample)))
        return out

    monkeypatch.setattr(module, "_units", units)


def test_a_short_unconfident_run_absorbed_into_a_confident_one_keeps_its_confidence(block_units):
    x, y, en = marked("weaktr", 60), marked("surede", 150), marked("sureen", 600)
    assert DEFAULT_CONFIDENT_MIN_RUN_CHARS <= len(y) and len(x + y) < DEFAULT_MIN_RUN_CHARS
    text, bounds = build(x, y, en)

    assert run(text, bounds, dominant="en", detector=marker_detector) == (
        Segment(0, len(x + y), "de"),
        Segment(len(x + y), len(text), "en"),
    )


def test_a_short_confident_run_absorbed_into_an_unconfident_one_gets_the_full_floor(block_units):
    x, y, en = marked("surede", 60), marked("weaktr", 150), marked("sureen", 600)
    assert DEFAULT_CONFIDENT_MIN_RUN_CHARS <= len(x + y) < DEFAULT_MIN_RUN_CHARS
    text, bounds = build(x, y, en)

    assert run(text, bounds, dominant="en", detector=marker_detector) == (
        Segment(0, len(text), "en"),
    )


@pytest.mark.parametrize(
    ("markers", "own"),
    [
        (["weaktr", "suretr"], False),
        (["suretr", "weaktr"], False),
        (["suretr", "weaktr", "suretr"], False),
        (["suretr", "suretr"], True),
    ],
)
def test_a_run_of_one_language_is_confident_only_when_every_unit_is(block_units, markers, own):
    blocks = [marked(marker, 100) for marker in markers]
    minority = "".join(blocks)
    assert DEFAULT_CONFIDENT_MIN_RUN_CHARS <= len(minority) < DEFAULT_MIN_RUN_CHARS
    en = marked("sureen", 600)
    text, bounds = build(en, *blocks, en)

    languages = [s.language for s in run(text, bounds, dominant="en", detector=marker_detector)]

    assert languages == (["en", "tr", "en"] if own else ["en"])


@pytest.mark.parametrize(
    ("language", "candidates", "confident"),
    [
        ("pt", [("pt-br", 0.6), ("pt-pt", 0.4)], True),
        ("zh", [("zh-cn", 0.5), ("zh-tw", 0.2)], False),
        ("en", [("tr", 0.97), ("en", 0.03)], False),
        ("tr", [("tr", 0.95)], True),
        ("tr", [("tr", 0.94), ("xx-yy-zz", 0.06)], False),
    ],
)
def test_confidence_sums_region_variants_of_the_resolved_language(language, candidates, confident):
    from septum_core.language_segments import _is_confident

    assert _is_confident(language, candidates) is confident


def test_a_short_run_confident_only_over_its_region_variants_keeps_its_own_segment():
    block = ("O contrato foi assinado pelas partes em Lisboa. " * 4)[:150]

    def regional(sample):
        return [("en", 0.99)] if " the " in sample else [("pt-br", 0.6), ("pt-pt", 0.4)]

    text, bounds = build(EN, block, EN)

    assert [s.language for s in run(text, bounds, dominant="en", detector=regional)] == [
        "en", "pt", "en",
    ]


def _counting(detector):
    calls = []

    def wrapped(sample):
        calls.append(sample)
        return detector(sample)

    return wrapped, calls


def test_one_letter_between_self_deciding_blocks_never_reaches_the_detector():
    import time

    han = dense_block("Han", 120)
    text, bounds = build(*[piece for _ in range(1600) for piece in ("a", han)])
    detector, calls = _counting(by_script_detector)

    started = time.perf_counter()
    segments = run(text, bounds, dominant="en", detector=detector)

    assert calls == []
    assert time.perf_counter() - started < 10
    assert segments == (Segment(0, len(text), "zh"),)


@pytest.mark.parametrize(("letters", "asked"), [(MIN_DETECTION_LETTERS - 1, False), (MIN_DETECTION_LETTERS, True)])
def test_a_unit_is_shown_to_the_detector_only_from_the_minimum_letter_count(letters, asked):
    unit = "x" * letters + " 1" * 20
    detector, calls = _counting(fake_detector)

    run(unit, [], dominant="en", detector=detector)

    assert (len(calls) == 1) is asked


def test_a_unit_with_too_few_letters_takes_its_neighbours_language():
    text, bounds = build(EN, "ve 12 34 56 78 90", TR)
    detector, calls = _counting(fake_detector)

    segments = run(text, bounds, dominant="en", detector=detector)

    assert len(calls) == 2
    assert [s.language for s in segments] == ["en", "tr"]
    assert segments[0].end == len(EN) + len("ve 12 34 56 78 90")


def test_a_confident_clause_at_its_floor_still_keeps_its_own_segment():
    clause = TR[:DEFAULT_CONFIDENT_MIN_RUN_CHARS]
    text, bounds = build(EN, clause, EN)

    assert [s.language for s in run(text, bounds, dominant="en")] == ["en", "tr", "en"]


@pytest.mark.parametrize(("budget", "split"), [(3, False), (4, True)])
def test_a_document_past_the_detector_call_budget_is_one_dominant_segment(
    monkeypatch, caplog, budget, split
):
    import septum_core.language_segments as module

    monkeypatch.setattr(module, "MAX_DETECTOR_CALLS_PER_DOCUMENT", budget)
    text, bounds = build(TR, EN, TR, EN)
    detector, calls = _counting(fake_detector)

    with caplog.at_level("WARNING"):
        segments = run(text, bounds, dominant="tr", detector=detector)

    assert len(calls) == min(budget, 4)
    if split:
        assert [s.language for s in segments] == ["tr", "en", "tr", "en"]
        assert caplog.text == ""
    else:
        assert segments == (Segment(0, len(text), "tr"),)
        assert str(budget) in caplog.text and "sözleşme" not in caplog.text
