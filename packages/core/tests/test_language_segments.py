"""Per-block language segmentation with an injected (fake) detector."""

from __future__ import annotations

import random

import pytest
from septum_core.language_resolution import _SCRIPT_RULES, deciding_script
from septum_core.language_segments import (
    COALESCE_CHARS,
    DEFAULT_MIN_RUN_CHARS,
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
    assert COALESCE_CHARS < len(small) < DEFAULT_MIN_RUN_CHARS
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
    assert len(calls) <= len(text) // COALESCE_CHARS + 1


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
def test_dense_script_run_below_its_floor_is_absorbed(script):
    block = dense_block(script, MIN_RUN_CHARS_BY_SCRIPT[script] - 10)
    text, bounds = build(EN, block, EN)
    assert run(text, bounds, dominant="en", detector=by_script_detector) == (
        Segment(0, len(text), "en"),
    )


def test_spaced_script_run_keeps_the_default_floor():
    under = TR[: DEFAULT_MIN_RUN_CHARS - 50]
    over = TR[: DEFAULT_MIN_RUN_CHARS + 50]
    assert run(*build(EN, under, EN), dominant="en") == (Segment(0, len(EN) * 2 + len(under), "en"),)
    kept = run(*build(EN, over, EN), dominant="en")
    assert [s.language for s in kept] == ["en", "tr", "en"]


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
