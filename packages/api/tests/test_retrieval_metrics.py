"""Hand-computed checks for the retrieval benchmark metric helpers."""

from __future__ import annotations

import math

import pytest

from tests.retrieval_metrics import (
    alignment,
    bootstrap_ci,
    classify_boundary,
    fragment_scorable,
    has_fragment_leak,
    has_literal_leak,
    mrr,
    name_tokens,
    ndcg_at_k,
    percentile,
    rank_gains,
    recall_at_k,
    sanitized_span,
)

GOLD = [(100, 200)]
MISS = (500, 600)
HIT = (90, 210)

EMAIL = "john.doe@example.com"
IBAN = "TR33 0006 1005 1978 6457 8413 26"


def _leaks(value: str, type_class: str, region: str) -> tuple[bool, bool]:
    return has_literal_leak(value, region), has_fragment_leak(value, type_class, region)


def test_mrr_first_relevant_at_rank_three():
    assert mrr([MISS, (700, 800), HIT], GOLD) == pytest.approx(1 / 3)


def test_mrr_no_relevant_is_zero():
    assert mrr([MISS], GOLD) == 0.0


def test_chunk_below_half_coverage_is_not_relevant():
    assert mrr([(100, 149)], GOLD) == 0.0
    assert mrr([(100, 150)], GOLD) == 1.0


def test_ndcg_ideal_ranking_is_one():
    gold = [(0, 10), (100, 110)]
    assert ndcg_at_k([(0, 10), (100, 110), MISS], gold, 10) == pytest.approx(1.0)


def test_ndcg_graded_gain_and_position():
    # Single gold, chunk covers 60% at rank 2: DCG = 0.6 / log2(3), ideal = 1.
    result = ndcg_at_k([MISS, (100, 160)], GOLD, 10)
    assert result == pytest.approx(0.6 / math.log2(3))


def test_overlapping_chunks_credit_gold_once():
    chunks = [(80, 180), (100, 200), (90, 210)]
    assert rank_gains(chunks, GOLD) == pytest.approx([0.8, 0.0, 0.0])
    assert ndcg_at_k(chunks, GOLD, 10) == pytest.approx(0.8)


def test_recall_at_k_with_two_gold_spans():
    gold = [(0, 10), (100, 110)]
    chunks = [(0, 10), MISS, (100, 110)]
    assert recall_at_k(chunks, gold, 1) == 0.5
    assert recall_at_k(chunks, gold, 2) == 0.5
    assert recall_at_k(chunks, gold, 3) == 1.0


def test_recall_counts_one_chunk_satisfying_two_gold_spans():
    assert recall_at_k([(0, 100)], [(0, 10), (50, 60)], 1) == 1.0


def test_percentile_known_values():
    values = [4, 1, 3, 2]
    assert percentile(values, 0) == 1
    assert percentile(values, 50) == 2.5
    assert percentile(values, 100) == 4
    assert percentile(list(range(1, 101)), 95) == pytest.approx(95.05)
    assert percentile([7], 95) == 7


def test_bootstrap_deterministic_and_bracketing():
    values = [0.0, 1.0, 1.0, 0.5, 0.25, 0.75, 1.0, 0.0]
    first = bootstrap_ci(values, seed=7)
    assert first == bootstrap_ci(values, seed=7)
    low, high = first
    assert low <= sum(values) / len(values) <= high


def test_bootstrap_constant_values_collapse():
    assert bootstrap_ci([0.5] * 5, seed=1) == (0.5, 0.5)


def test_email_not_leaked_when_only_domain_word_survives():
    # Compact value "johndoeexamplecom" (17) needs a run of max(6, 9) = 9;
    # "example" alone is 7 characters.
    assert _leaks(EMAIL, "identifier", "contact example support") == (False, False)


def test_email_fragment_leaked_when_run_of_nine_survives():
    # "doeexample" (10 compact characters) is a contiguous part of the value.
    assert _leaks(EMAIL, "identifier", "doe example") == (False, True)


def test_email_literal_leak_implies_fragment_leak():
    assert _leaks(EMAIL, "identifier", "write to John.Doe@example.com today") == (True, True)


def test_iban_partial_at_threshold_leaks_and_below_does_not():
    # Compact IBAN "TR330006100519786457841326" (26): run = max(6, 13) = 13.
    # Compact positions 12..25 are "19786457841326" (14 characters).
    assert has_fragment_leak(IBAN, "identifier", "x 1978645784132 y") is True
    assert has_fragment_leak(IBAN, "identifier", "x 197864578413 y") is False


def test_iban_scattered_parts_do_not_add_up():
    # "6457 8413 26" gives 10 contiguous characters; below the run of 13.
    assert has_fragment_leak(IBAN, "identifier", "pay 6457 8413 26") is False


def test_identifier_fully_masked_is_clean():
    assert _leaks(IBAN, "identifier", "[IBAN_1]") == (False, False)
    assert _leaks(EMAIL, "identifier", "call 123 456 7") == (False, False)


def test_identifier_shorter_than_six_is_not_fragment_scorable():
    assert fragment_scorable("AB12", "identifier") is False
    assert has_fragment_leak("AB12", "identifier", "AB12") is False
    assert has_literal_leak("AB12", "AB12") is True


def test_placeholder_is_never_a_leak():
    assert _leaks("Person Name", "name", "[PERSON_NAME_1]") == (False, False)
    assert _leaks("PERSON 1234", "identifier", "[PERSON_NAME_1]") == (False, False)
    assert _leaks("[PERSON_NAME_1]", "name", "[PERSON_NAME_1]") == (False, False)


def test_name_whole_word_fragment_only():
    assert has_fragment_leak("Ayse Yilmaz", "name", "Dear yilmaz, welcome") is True
    assert has_fragment_leak("Ayse Yilmaz", "name", "Yilmazlar family") is False


def test_name_leak_unicode_case_insensitive():
    assert has_fragment_leak("Şükrü Öztürk", "name", "bay ÖZTÜRK geldi") is True


def test_latin_name_two_letters_is_not_scorable():
    assert name_tokens("Al") == []
    assert fragment_scorable("Al", "name") is False
    assert has_fragment_leak("Al", "name", "al") is False


def test_two_char_cjk_name_leaked_when_surviving():
    assert name_tokens("李明") == ["李明"]
    assert has_fragment_leak("李明", "name", "会议由李明主持") is True
    assert has_fragment_leak("李明", "name", "王芳主持") is False


def test_single_cjk_character_is_not_scorable():
    assert fragment_scorable("李", "name") is False


def test_unspaced_name_partial_run_threshold():
    # 8 characters: run = max(2, 4) = 4.
    name = "北辰科技有限公司"
    assert has_fragment_leak(name, "name", "科技") is False
    assert has_fragment_leak(name, "name", "北辰科技") is True


def test_name_tokens_keep_unspaced_run_whole():
    assert name_tokens("北辰 科技·有限公司") == ["北辰", "科技", "有限公司"]


def test_unknown_type_class_rejected():
    with pytest.raises(ValueError):
        has_fragment_leak("x", "other", "x")
    with pytest.raises(ValueError):
        fragment_scorable("x", "other")


def test_sanitized_span_maps_replacement_only():
    raw = "aaaa VALUE bbbb"
    sanitized = "aaaa [X_1] bbbb"
    start, end = raw.index("VALUE"), raw.index("VALUE") + len("VALUE")
    lo, hi = sanitized_span(alignment(raw, sanitized), start, end)
    assert sanitized[lo:hi] == "[X_1]"


def test_sanitized_span_keeps_surviving_part_of_name():
    raw = "Ayse Yilmaz met"
    sanitized = "[PERSON_NAME_1] Yilmaz met"
    lo, hi = sanitized_span(alignment(raw, sanitized), 0, 11)
    assert sanitized[lo:hi] == "[PERSON_NAME_1] Yilmaz"
    assert has_fragment_leak("Ayse Yilmaz", "name", sanitized[lo:hi]) is True


def test_sanitized_span_empty_when_nothing_maps():
    assert sanitized_span(alignment("abc", "abc"), 5, 9) == (0, 0)


def test_boundary_classification():
    chunks = [(0, 100), (100, 200)]
    assert classify_boundary((90, 110), chunks) == "crossing"
    assert classify_boundary((10, 20), chunks) == "control"
    assert classify_boundary((100, 120), chunks) == "control"
    assert classify_boundary(None, chunks) == "unknown"
