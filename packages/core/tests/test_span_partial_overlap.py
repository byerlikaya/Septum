from __future__ import annotations

import random

import pytest
from ner_fakes import FakePipeline, StubRegistry
from septum_core import SeptumCoreConfig, SeptumEngine
from septum_core.ner_model_registry import _WindowedNERPipeline
from septum_core.ports import NullSemanticDetectionPort
from septum_core.anonymization_map import AnonymizationMap
from septum_core.detector import Detector
from septum_core.span_processing import (
    _tail_starts_with_letter,
    absorb_overlapping_spans,
    deduplicate_spans,
    expand_person_name_spans,
    merge_adjacent_person_name_spans,
)
from septum_core.spans import DetectedSpan

HIGH = {"PHONE_NUMBER", "NATIONAL_ID", "IBAN"}

# Three-letter words split by one space: word k spans [4k, 4k + 3). Cut points
# at word edges are real token boundaries, so no remainder folds into a winner.
WORDS_TEXT = "aaa bbb ccc ddd eee fff"


def _words(first: int, last: int) -> tuple[int, int]:
    return first * 4, last * 4 + 3


def _span(start: int, end: int, entity_type: str, score: float = 0.9) -> DetectedSpan:
    return DetectedSpan(start=start, end=end, entity_type=entity_type, score=score)


def _triples(spans: list[DetectedSpan]) -> list[tuple[int, int, str]]:
    return [(s.start, s.end, s.entity_type) for s in spans]


def _covered(spans: list[DetectedSpan]) -> set[int]:
    return {i for s in spans for i in range(s.start, s.end)}


def _assert_no_coverage_lost(
    text: str, inputs: list[DetectedSpan], outputs: list[DetectedSpan]
) -> None:
    lost = [
        i
        for i in _covered(inputs)
        if text[i].isalnum() and i not in _covered(outputs)
    ]
    assert lost == [], f"characters left unmasked: {[text[i] for i in lost]!r}"


def _assert_disjoint(outputs: list[DetectedSpan]) -> None:
    ordered = sorted(outputs, key=lambda s: s.start)
    for left, right in zip(ordered, ordered[1:]):
        assert left.end <= right.start, f"overlapping output spans {left} {right}"


CJK_TEXT = "请联系zhang.wei@example.com获取资料"
CJK_EMAIL = (3, 24)
CJK_NAME = (1, 8)  # "联系zhang" as a NER PERSON fragment


def test_cjk_email_partially_overlapped_by_person_is_one_email() -> None:
    inputs = [
        _span(*CJK_NAME, "PERSON_NAME", 0.9),
        _span(*CJK_EMAIL, "EMAIL_ADDRESS", 0.8),
    ]
    out = deduplicate_spans(inputs, HIGH, text=CJK_TEXT)

    emails = [(s.start, s.end) for s in out if s.entity_type == "EMAIL_ADDRESS"]
    assert emails == [CJK_EMAIL]
    _assert_disjoint(out)
    _assert_no_coverage_lost(CJK_TEXT, inputs, out)


def test_cjk_email_wins_regardless_of_input_order() -> None:
    inputs = [
        _span(*CJK_EMAIL, "EMAIL_ADDRESS", 0.8),
        _span(*CJK_NAME, "PERSON_NAME", 0.9),
    ]
    forward = deduplicate_spans(inputs, HIGH, text=CJK_TEXT)
    backward = deduplicate_spans(list(reversed(inputs)), HIGH, text=CJK_TEXT)

    assert forward == backward
    assert _triples(forward) == [(1, 3, "PERSON_NAME"), (3, 24, "EMAIL_ADDRESS")]


def test_latin_email_partially_overlapped_by_person_is_one_email() -> None:
    text = "Write to zhang.wei@example.com now"
    email_start = text.index("zhang")
    email = (email_start, email_start + len("zhang.wei@example.com"))
    name = (text.index("to"), email_start + len("zhang"))
    inputs = [_span(*name, "PERSON_NAME", 0.9), _span(*email, "EMAIL_ADDRESS", 1.0)]
    out = deduplicate_spans(inputs, HIGH, text=text)

    emails = [(s.start, s.end) for s in out if s.entity_type == "EMAIL_ADDRESS"]
    assert emails == [email]
    _assert_disjoint(out)
    _assert_no_coverage_lost(text, inputs, out)


def test_partial_overlap_within_high_pool_higher_type_wins_and_keeps_tail() -> None:
    phone = _span(*_words(0, 1), "PHONE_NUMBER", 0.8)
    national = _span(*_words(1, 2), "NATIONAL_ID", 0.6)
    out = deduplicate_spans([phone, national], HIGH, text=WORDS_TEXT)

    assert _triples(out) == [(0, 3, "PHONE_NUMBER"), (4, 11, "NATIONAL_ID")]
    _assert_disjoint(out)
    _assert_no_coverage_lost(WORDS_TEXT, [phone, national], out)


def test_partial_overlap_decided_by_pool_flag_over_type_priority() -> None:
    # NATIONAL_ID has the higher type priority (95 vs 70), but PHONE_NUMBER is
    # the only high-pool type here, so the pool flag must decide the overlap.
    national = _span(*_words(0, 1), "NATIONAL_ID", 0.9)
    phone = _span(*_words(1, 2), "PHONE_NUMBER", 0.6)
    out = deduplicate_spans([national, phone], {"PHONE_NUMBER"}, text=WORDS_TEXT)

    assert _triples(out) == [(0, 3, "NATIONAL_ID"), (4, 11, "PHONE_NUMBER")]
    _assert_disjoint(out)
    _assert_no_coverage_lost(WORDS_TEXT, [national, phone], out)


def test_partial_overlap_without_text_keeps_remainder_untrimmed() -> None:
    inputs = [_span(0, 6, "PERSON_NAME"), _span(4, 9, "LOCATION")]
    out = deduplicate_spans(inputs, HIGH)

    assert _triples(out) == [(0, 6, "PERSON_NAME"), (6, 9, "LOCATION")]


def test_equal_rank_partial_overlap_keeps_earlier_span_and_newcomer_remainder() -> None:
    inputs = [_span(*_words(0, 1), "PERSON_NAME"), _span(*_words(1, 2), "LOCATION")]
    out = deduplicate_spans(inputs, HIGH, text=WORDS_TEXT)

    assert _triples(out) == [(0, 7, "PERSON_NAME"), (8, 11, "LOCATION")]
    assert deduplicate_spans(list(reversed(inputs)), HIGH, text=WORDS_TEXT) == out


def test_chained_partial_overlaps_are_cut_in_start_order() -> None:
    person = _span(*_words(0, 1), "PERSON_NAME")
    location = _span(*_words(1, 2), "LOCATION")
    org = _span(*_words(2, 3), "ORGANIZATION_NAME")
    inputs = [org, location, person]
    out = deduplicate_spans(inputs, HIGH, text=WORDS_TEXT)

    assert _triples(out) == [
        (0, 7, "PERSON_NAME"),
        (8, 11, "LOCATION"),
        (12, 15, "ORGANIZATION_NAME"),
    ]
    _assert_disjoint(out)
    _assert_no_coverage_lost(WORDS_TEXT, inputs, out)


def test_newcomer_outranking_one_overlapped_span_still_yields_to_the_other() -> None:
    person = _span(0, 3, "PERSON_NAME")
    phone = _span(8, 15, "PHONE_NUMBER")
    email = _span(2, 10, "EMAIL_ADDRESS")
    inputs = [person, phone, email]
    out = deduplicate_spans(inputs, HIGH, text=WORDS_TEXT)

    assert _triples(out) == [
        (0, 3, "PERSON_NAME"),
        (4, 7, "EMAIL_ADDRESS"),
        (8, 15, "PHONE_NUMBER"),
    ]
    _assert_disjoint(out)
    _assert_no_coverage_lost(WORDS_TEXT, inputs, out)


@pytest.mark.parametrize("structured", ["IP_ADDRESS", "MAC_ADDRESS"])
def test_email_beats_ip_or_mac_on_partial_overlap_whatever_the_order(
    structured: str,
) -> None:
    struct = _span(*_words(1, 2), structured)
    email = _span(*_words(0, 1), "EMAIL_ADDRESS")
    out_email_first = deduplicate_spans([email, struct], HIGH, text=WORDS_TEXT)
    assert _triples(out_email_first) == [(0, 7, "EMAIL_ADDRESS"), (8, 11, structured)]

    email_later = _span(*_words(1, 2), "EMAIL_ADDRESS")
    struct_first = _span(*_words(0, 1), structured)
    out_struct_first = deduplicate_spans(
        [struct_first, email_later], HIGH, text=WORDS_TEXT
    )
    assert _triples(out_struct_first) == [(0, 3, structured), (4, 11, "EMAIL_ADDRESS")]


def test_email_and_phone_in_high_pool_are_decided_by_type_priority() -> None:
    both = {"PHONE_NUMBER", "EMAIL_ADDRESS"}
    email = _span(*_words(0, 1), "EMAIL_ADDRESS")
    phone = _span(*_words(1, 2), "PHONE_NUMBER")
    out = deduplicate_spans([email, phone], both, text=WORDS_TEXT)

    assert _triples(out) == [(0, 3, "EMAIL_ADDRESS"), (4, 11, "PHONE_NUMBER")]
    assert deduplicate_spans([phone, email], both, text=WORDS_TEXT) == out


def test_exact_offset_tie_still_prefers_type_priority() -> None:
    inputs = [_span(4, 16, "PHONE_NUMBER", 0.8), _span(4, 16, "NATIONAL_ID", 0.6)]
    out = deduplicate_spans(inputs, HIGH, text="Tel 5551234567 x")

    assert _triples(out) == [(4, 16, "NATIONAL_ID")]


def test_same_start_longer_span_wins() -> None:
    text = "abcdefghi"
    inputs = [_span(0, 5, "PERSON_NAME"), _span(0, 9, "LOCATION")]
    out = deduplicate_spans(inputs, HIGH, text=text)

    assert _triples(out) == [(0, 9, "LOCATION")]


def test_same_span_score_decides_the_winner() -> None:
    text = "abcde"
    inputs = [_span(0, 5, "PERSON_NAME", 0.6), _span(0, 5, "LOCATION", 0.9)]
    out = deduplicate_spans(inputs, HIGH, text=text)

    assert out == [_span(0, 5, "LOCATION", 0.9)]
    assert deduplicate_spans(list(reversed(inputs)), HIGH, text=text) == out


def test_remainder_keeps_container_score() -> None:
    inputs = [
        _span(0, 11, "PERSON_NAME", 0.7),
        _span(*_words(1, 1), "PHONE_NUMBER", 0.9),
    ]
    out = deduplicate_spans(inputs, HIGH, text=WORDS_TEXT)

    assert out == [
        _span(0, 3, "PERSON_NAME", 0.7),
        _span(4, 7, "PHONE_NUMBER", 0.9),
        _span(8, 11, "PERSON_NAME", 0.7),
    ]


def test_equal_type_partial_overlap_merges_into_union_with_max_score() -> None:
    inputs = [_span(0, 6, "PERSON_NAME", 0.7), _span(4, 10, "PERSON_NAME", 0.9)]
    out = deduplicate_spans(inputs, HIGH, text="abcdefghij")

    assert out == [_span(0, 10, "PERSON_NAME", 0.9)]


def test_union_keeps_the_higher_score_when_the_earlier_span_has_it() -> None:
    inputs = [
        _span(*_words(0, 1), "PERSON_NAME", 0.9),
        _span(*_words(1, 2), "PERSON_NAME", 0.7),
    ]
    out = deduplicate_spans(inputs, HIGH, text=WORDS_TEXT)

    assert out == [_span(0, 11, "PERSON_NAME", 0.9)]


def test_equal_type_containment_keeps_container_with_max_score() -> None:
    text = "abcdefghij"
    inputs = [_span(0, 10, "PERSON_NAME", 0.6), _span(2, 4, "PERSON_NAME", 0.95)]
    out = deduplicate_spans(inputs, HIGH, text=text)

    assert out == [_span(0, 10, "PERSON_NAME", 0.95)]


@pytest.mark.parametrize(
    ("text", "container", "phone", "expected_parts"),
    [
        pytest.param(
            "xx.@( 5551234 )@!yy",
            (0, 19),
            (6, 13),
            [(0, 2, "PERSON_NAME"), (6, 13, "PHONE_NUMBER"), (17, 19, "PERSON_NAME")],
            id="dot-at-paren-remnants",
        ),
        pytest.param(
            "ab\t\n  7777  \ncd",
            (0, 15),
            (6, 10),
            [(0, 2, "PERSON_NAME"), (6, 10, "PHONE_NUMBER"), (13, 15, "PERSON_NAME")],
            id="tab-newline-nbsp-whitespace",
        ),
        pytest.param(
            "(.) 5555",
            (0, 8),
            (4, 8),
            [(4, 8, "PHONE_NUMBER")],
            id="remainder-without-alphanumeric-is-dropped",
        ),
        pytest.param(
            "请(5555)",
            (0, 7),
            (2, 6),
            [(0, 1, "PERSON_NAME"), (2, 6, "PHONE_NUMBER")],
            id="cjk-counts-as-alphanumeric",
        ),
    ],
)
def test_remainders_are_trimmed_to_alphanumeric_edges(
    text: str,
    container: tuple[int, int],
    phone: tuple[int, int],
    expected_parts: list[tuple[int, int, str]],
) -> None:
    inputs = [_span(*container, "PERSON_NAME", 0.7), _span(*phone, "PHONE_NUMBER", 0.9)]
    out = deduplicate_spans(inputs, HIGH, text=text)

    assert _triples(out) == expected_parts
    _assert_disjoint(out)
    _assert_no_coverage_lost(text, inputs, out)


@pytest.mark.parametrize("separator", [".", "_"])
def test_single_non_word_remainder_is_dropped(separator: str) -> None:
    text = "aaa" + separator + "bbb"
    phone = _span(0, 3, "PHONE_NUMBER")
    person = _span(0, 4, "PERSON_NAME")
    out = deduplicate_spans([phone, person], HIGH, text=text)

    assert _triples(out) == [(0, 3, "PHONE_NUMBER")]


def test_remainder_of_only_combining_marks_is_dropped() -> None:
    text = "aaa ́ "
    phone = _span(0, 3, "PHONE_NUMBER")
    person = _span(0, 6, "PERSON_NAME")
    out = deduplicate_spans([phone, person], HIGH, text=text)

    assert _triples(out) == [(0, 3, "PHONE_NUMBER")]


@pytest.mark.parametrize(
    ("text", "container", "phone", "expected_parts"),
    [
        pytest.param(
            "กิ 5555",
            (0, 7),
            (3, 7),
            [(0, 2, "PERSON_NAME"), (3, 7, "PHONE_NUMBER")],
            id="thai-vowel-sign-at-trailing-edge-stays-masked",
        ),
        pytest.param(
            "कि 5555",
            (0, 7),
            (3, 7),
            [(0, 2, "PERSON_NAME"), (3, 7, "PHONE_NUMBER")],
            id="devanagari-vowel-sign-at-trailing-edge-stays-masked",
        ),
        pytest.param(
            "ẍ 5555 ̈y",
            (0, 10),
            (3, 7),
            [(0, 2, "PERSON_NAME"), (3, 7, "PHONE_NUMBER"), (8, 10, "PERSON_NAME")],
            id="decomposed-latin-plus-combining-diaeresis-at-both-edges",
        ),
    ],
)
def test_combining_marks_at_remainder_edges_stay_masked(
    text: str,
    container: tuple[int, int],
    phone: tuple[int, int],
    expected_parts: list[tuple[int, int, str]],
) -> None:
    inputs = [_span(*container, "PERSON_NAME", 0.7), _span(*phone, "PHONE_NUMBER", 0.9)]
    out = deduplicate_spans(inputs, HIGH, text=text)

    assert _triples(out) == expected_parts
    _assert_no_coverage_lost(text, inputs, out)


def test_apostrophe_suffix_joins_the_winner_token() -> None:
    text = "Ayşe Kaya'nın evi"
    person = _span(0, 9, "PERSON_NAME", 0.9)
    location = _span(5, 13, "LOCATION", 0.7)
    out = deduplicate_spans([person, location], HIGH, text=text)

    assert _triples(out) == [(0, 13, "PERSON_NAME")]
    _assert_no_coverage_lost(text, [person, location], out)


def test_typographic_apostrophe_suffix_joins_the_winner_token() -> None:
    text = "Ayşe Kaya\u2019nın evi"
    person = _span(0, 9, "PERSON_NAME", 0.9)
    location = _span(5, 13, "LOCATION", 0.7)
    out = deduplicate_spans([person, location], HIGH, text=text)

    assert _triples(out) == [(0, 13, "PERSON_NAME")]


def test_mid_token_cut_on_the_left_is_folded_into_the_newcomer() -> None:
    text = "Ayşe Kaya'nın"
    location = _span(0, 9, "LOCATION", 0.7)
    email = _span(6, 13, "EMAIL_ADDRESS", 0.9)
    out = deduplicate_spans([location, email], HIGH, text=text)

    assert _triples(out) == [(0, 4, "LOCATION"), (5, 13, "EMAIL_ADDRESS")]
    _assert_no_coverage_lost(text, [location, email], out)


def test_quote_after_a_name_is_not_a_suffix() -> None:
    text = "Ayşe Kaya' evi"
    person = _span(0, 9, "PERSON_NAME", 0.9)
    location = _span(5, 10, "LOCATION", 0.7)
    out = deduplicate_spans([person, location], HIGH, text=text)

    assert _triples(out) == [(0, 9, "PERSON_NAME")]


def test_cjk_remainder_across_a_glyph_break_stays_a_separate_token() -> None:
    text = "北京市朝阳区"
    person = _span(0, 2, "PERSON_NAME", 0.9)
    location = _span(1, 4, "LOCATION", 0.9)
    out = deduplicate_spans([person, location], HIGH, text=text)

    assert _triples(out) == [(0, 2, "PERSON_NAME"), (2, 4, "LOCATION")]


@pytest.mark.parametrize(
    ("outer_type", "inner_type"),
    [
        pytest.param("LOCATION", "URL", id="url-over-location"),
        pytest.param("PERSON_NAME", "IP_ADDRESS", id="ip-over-person"),
        pytest.param("LOCATION", "MAC_ADDRESS", id="mac-over-location"),
    ],
)
def test_structured_identifier_wins_partial_overlap_with_generic_type(
    outer_type: str, inner_type: str
) -> None:
    inputs = [_span(*_words(0, 1), outer_type), _span(*_words(1, 2), inner_type)]
    out = deduplicate_spans(inputs, HIGH, text=WORDS_TEXT)

    assert _triples(out) == [(0, 3, outer_type), (4, 11, inner_type)]
    _assert_no_coverage_lost(WORDS_TEXT, inputs, out)


def test_email_beats_url_on_partial_overlap() -> None:
    inputs = [_span(*_words(0, 1), "URL"), _span(*_words(1, 2), "EMAIL_ADDRESS")]
    out = deduplicate_spans(inputs, HIGH, text=WORDS_TEXT)

    assert _triples(out) == [(0, 3, "URL"), (4, 11, "EMAIL_ADDRESS")]


def test_url_inside_email_is_dropped() -> None:
    text = "abcdefghijklmnopqrst"
    inputs = [_span(0, 20, "EMAIL_ADDRESS"), _span(5, 12, "URL")]
    out = deduplicate_spans(inputs, HIGH, text=text)

    assert _triples(out) == [(0, 20, "EMAIL_ADDRESS")]


def test_non_overlapping_spans_pass_through_unchanged() -> None:
    inputs = [
        _span(0, 4, "PERSON_NAME", 0.9),
        _span(10, 20, "PHONE_NUMBER", 0.8),
        _span(25, 30, "EMAIL_ADDRESS", 1.0),
    ]
    out = deduplicate_spans(inputs, HIGH, text=" " * 40)

    assert out == sorted(inputs, key=lambda s: s.start)


def test_touching_spans_stay_separate() -> None:
    same_type = [_span(0, 3, "PERSON_NAME"), _span(3, 6, "PERSON_NAME")]
    out = deduplicate_spans(same_type, HIGH, text="abcdef")
    assert _triples(out) == [(0, 3, "PERSON_NAME"), (3, 6, "PERSON_NAME")]

    cross_type = [_span(0, 3, "PERSON_NAME"), _span(3, 6, "PHONE_NUMBER")]
    out = deduplicate_spans(cross_type, HIGH, text="abcdef")
    assert _triples(out) == [(0, 3, "PERSON_NAME"), (3, 6, "PHONE_NUMBER")]


def test_low_span_fully_inside_high_span_is_dropped() -> None:
    text = "Tel 5551234567 x"
    inputs = [_span(4, 14, "PHONE_NUMBER", 0.8), _span(6, 9, "PERSON_NAME", 0.9)]
    out = deduplicate_spans(inputs, HIGH, text=text)

    assert _triples(out) == [(4, 14, "PHONE_NUMBER")]


def test_low_container_of_high_span_keeps_its_remainder() -> None:
    text = "Ali 5551234567 ok"
    inputs = [_span(0, 14, "PERSON_NAME", 0.9), _span(4, 14, "PHONE_NUMBER", 0.8)]
    out = deduplicate_spans(inputs, HIGH, text=text)

    assert _triples(out) == [(0, 3, "PERSON_NAME"), (4, 14, "PHONE_NUMBER")]
    _assert_no_coverage_lost(text, inputs, out)


def test_zero_length_and_reversed_spans_are_discarded() -> None:
    text = "abcdef"
    kept = deduplicate_spans(
        [_span(2, 2, "PERSON_NAME"), _span(4, 6, "LOCATION")], HIGH, text=text
    )
    assert _triples(kept) == [(4, 6, "LOCATION")]

    reversed_only = deduplicate_spans([_span(6, 2, "PERSON_NAME")], HIGH, text=text)
    assert reversed_only == []


def test_zero_length_span_does_not_erase_a_real_span() -> None:
    inputs = [_span(5, 13, "PERSON_NAME"), _span(8, 8, "PHONE_NUMBER")]
    out = deduplicate_spans(inputs, HIGH)

    assert _triples(out) == [(5, 13, "PERSON_NAME")]


def test_negative_offsets_are_clamped_or_dropped() -> None:
    inputs = [_span(-5, 3, "PERSON_NAME"), _span(-5, -2, "LOCATION")]
    out = deduplicate_spans(inputs, HIGH, text="abcdef")

    assert _triples(out) == [(0, 3, "PERSON_NAME")]


def test_empty_text_drops_every_span() -> None:
    assert deduplicate_spans([_span(0, 3, "PERSON_NAME")], HIGH, text="") == []


def test_offsets_are_clamped_to_text_bounds() -> None:
    text = "aaa bbb ccc ddd"
    inputs = [_span(0, 50, "PERSON_NAME"), _span(4, 7, "PHONE_NUMBER")]
    out = deduplicate_spans(inputs, HIGH, text=text)

    assert _triples(out) == [
        (0, 3, "PERSON_NAME"),
        (4, 7, "PHONE_NUMBER"),
        (8, 15, "PERSON_NAME"),
    ]
    _assert_no_coverage_lost(text, [_span(0, 15, "PERSON_NAME"), inputs[1]], out)


def test_span_beyond_text_end_is_clamped_not_raised() -> None:
    out = deduplicate_spans([_span(4, 50, "LOCATION")], HIGH, text="abcdef")

    assert _triples(out) == [(4, 6, "LOCATION")]


def test_cut_at_span_edge_without_text_leaves_no_empty_span() -> None:
    inputs = [_span(0, 6, "PERSON_NAME"), _span(0, 3, "PHONE_NUMBER")]
    out = deduplicate_spans(inputs, HIGH)

    assert _triples(out) == [(0, 3, "PHONE_NUMBER"), (3, 6, "PERSON_NAME")]


def test_absorption_trims_partial_location_and_keeps_its_tail() -> None:
    text = "ab" * 10 + "a" + " " + "ba" * 9
    org = _span(0, 21, "ORGANIZATION_NAME", 0.9)
    loc = _span(13, 34, "LOCATION", 0.95)
    absorbed = absorb_overlapping_spans([loc, org], text=text)
    out = deduplicate_spans(absorbed, HIGH, text=text)

    assert _triples(out) == [(0, 21, "ORGANIZATION_NAME"), (22, 34, "LOCATION")]
    _assert_no_coverage_lost(text, [loc, org], out)


def test_absorption_folds_a_cut_mid_token_into_the_outer_span() -> None:
    text = "aaa bbbcccdd"
    org = _span(0, 7, "ORGANIZATION_NAME", 0.9)
    loc = _span(4, 12, "LOCATION", 0.8)
    out = absorb_overlapping_spans([org, loc], text=text)

    assert _triples(out) == [(0, 12, "ORGANIZATION_NAME")]


def test_absorb_remainder_keeps_the_inner_score() -> None:
    org = _span(0, 5, "ORGANIZATION_NAME", 0.9)
    loc = _span(3, 9, "LOCATION", 0.42)
    out = absorb_overlapping_spans([org, loc])

    assert out == [_span(0, 5, "ORGANIZATION_NAME", 0.9), _span(5, 9, "LOCATION", 0.42)]


def test_postal_address_absorbs_street_address_partial_overlap() -> None:
    postal = _span(0, 10, "POSTAL_ADDRESS")
    street = _span(5, 15, "STREET_ADDRESS")
    out = absorb_overlapping_spans([postal, street])

    assert _triples(out) == [(0, 10, "POSTAL_ADDRESS"), (10, 15, "STREET_ADDRESS")]


def test_street_address_absorbs_location_partial_overlap() -> None:
    street = _span(0, 10, "STREET_ADDRESS")
    location = _span(5, 15, "LOCATION")
    out = absorb_overlapping_spans([street, location])

    assert _triples(out) == [(0, 10, "STREET_ADDRESS"), (10, 15, "LOCATION")]


def test_one_inner_location_clipped_by_two_organizations_keeps_the_gap() -> None:
    location = _span(*_words(0, 2), "LOCATION")
    org_a = _span(*_words(0, 0), "ORGANIZATION_NAME")
    org_b = _span(*_words(2, 2), "ORGANIZATION_NAME")
    out = absorb_overlapping_spans([location, org_a, org_b], text=WORDS_TEXT)

    assert sorted(_triples(out)) == [
        (0, 3, "ORGANIZATION_NAME"),
        (4, 7, "LOCATION"),
        (8, 11, "ORGANIZATION_NAME"),
    ]


def _random_case(rng: random.Random) -> tuple[str, list[DetectedSpan]]:
    alphabet = "ab 1@.请"
    text = "".join(rng.choice(alphabet) for _ in range(30))
    types = [
        "PERSON_NAME",
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "NATIONAL_ID",
        "LOCATION",
        "URL",
    ]
    spans = []
    for _ in range(rng.randint(1, 6)):
        start = rng.randrange(0, 29)
        end = min(30, start + rng.randint(1, 12))
        score = rng.choice((0.5, 0.6, 0.8, 0.9))
        spans.append(_span(start, end, rng.choice(types), score))
    return text, spans


@pytest.mark.parametrize("seed", range(300))
def test_random_overlaps_keep_coverage_and_never_invent_it(seed: int) -> None:
    text, inputs = _random_case(random.Random(seed))
    out = deduplicate_spans(inputs, HIGH, text=text)

    _assert_disjoint(out)
    _assert_no_coverage_lost(text, inputs, out)
    for s in out:
        for pos in range(s.start, s.end):
            assert any(i.start <= pos < i.end for i in inputs), (s, pos)


def _dense_case(rng: random.Random) -> tuple[str, list[DetectedSpan]]:
    alphabet = "abZ1 @.(请文\n\t ́"
    text = "".join(rng.choice(alphabet) for _ in range(80))
    types = [
        "PERSON_NAME",
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "NATIONAL_ID",
        "LOCATION",
        "URL",
        "IP_ADDRESS",
        "ORGANIZATION_NAME",
    ]
    spans = []
    for _ in range(rng.randint(1, 40)):
        start = rng.randrange(0, 79)
        end = min(80, start + rng.randint(1, 20))
        score = rng.choice((0.5, 0.6, 0.8, 0.9))
        spans.append(_span(start, end, rng.choice(types), score))
    return text, spans


@pytest.mark.parametrize("seed", range(200))
def test_dense_mixed_script_overlaps_keep_invariants(seed: int) -> None:
    text, inputs = _dense_case(random.Random(seed))
    out = deduplicate_spans(inputs, HIGH, text=text)

    _assert_disjoint(out)
    _assert_no_coverage_lost(text, inputs, out)
    for s in out:
        assert 0 <= s.start < s.end <= len(text)
        for pos in range(s.start, s.end):
            assert any(i.start <= pos < i.end for i in inputs), (s, pos)


def test_cjk_email_leak_is_closed_end_to_end() -> None:
    config = SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=True)
    pipeline = FakePipeline(emit={"联系zhang": "PER"})
    registry = StubRegistry(_WindowedNERPipeline(pipeline, None))
    engine = SeptumEngine(
        regulations=["gdpr"],
        config=config,
        ner_registry=registry,
        semantic_port=NullSemanticDetectionPort(),
    )

    result = engine.mask(CJK_TEXT, language="zh")

    assert result.masked_text.count("[EMAIL_ADDRESS_") == 1
    for fragment in ("zhang", "wei", "example", "@"):
        assert fragment not in result.masked_text, result.masked_text
    assert engine.unmask(result.masked_text, result.session_id) == CJK_TEXT


def test_latin_email_leak_is_closed_end_to_end() -> None:
    text = "Mail zhang.wei@example.com now"
    config = SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=True)
    pipeline = FakePipeline(emit={"Mail zhang": "PER"})
    registry = StubRegistry(_WindowedNERPipeline(pipeline, None))
    engine = SeptumEngine(
        regulations=["gdpr"],
        config=config,
        ner_registry=registry,
        semantic_port=NullSemanticDetectionPort(),
    )

    result = engine.mask(text, language="en")

    assert result.masked_text.count("[EMAIL_ADDRESS_") == 1
    for fragment in ("zhang", "wei", "example", "@"):
        assert fragment not in result.masked_text, result.masked_text


def test_apostrophe_as_last_character_never_joins_a_token() -> None:
    text = "abcd'"
    inputs = [_span(0, 2, "PERSON_NAME"), _span(1, 5, "LOCATION")]
    out = deduplicate_spans(inputs, HIGH, text=text)

    assert _triples(out) == [(0, 4, "PERSON_NAME")]


def test_apostrophe_at_text_start_does_not_fold_the_following_letters() -> None:
    text = "'abc"
    inputs = [_span(0, 1, "ORGANIZATION_NAME"), _span(0, 4, "LOCATION")]
    out = absorb_overlapping_spans(inputs, text=text)

    assert sorted(_triples(out)) == [(0, 1, "ORGANIZATION_NAME"), (1, 4, "LOCATION")]


def test_apostrophe_after_a_space_does_not_fold_into_the_previous_word() -> None:
    text = "Ab 'cd"
    inputs = [_span(0, 3, "PERSON_NAME"), _span(2, 6, "LOCATION")]
    out = deduplicate_spans(inputs, HIGH, text=text)

    assert _triples(out) == [(0, 3, "PERSON_NAME"), (4, 6, "LOCATION")]


def test_apostrophe_before_a_space_does_not_fold_into_the_next_word() -> None:
    text = "Ab' cd"
    inputs = [_span(0, 2, "PERSON_NAME"), _span(1, 6, "LOCATION")]
    out = deduplicate_spans(inputs, HIGH, text=text)

    assert _triples(out) == [(0, 2, "PERSON_NAME"), (4, 6, "LOCATION")]


def test_left_fold_stops_at_the_token_boundary() -> None:
    text = "abcde fgh"
    inputs = [_span(0, 3, "PERSON_NAME"), _span(2, 8, "LOCATION")]
    out = deduplicate_spans(inputs, HIGH, text=text)

    assert _triples(out) == [(0, 5, "PERSON_NAME"), (6, 8, "LOCATION")]
    _assert_no_coverage_lost(text, inputs, out)


def test_left_fold_does_not_cross_a_script_break() -> None:
    text = "Ab请cd"
    inputs = [_span(0, 2, "PERSON_NAME"), _span(1, 5, "LOCATION")]
    out = deduplicate_spans(inputs, HIGH, text=text)

    assert _triples(out) == [(0, 2, "PERSON_NAME"), (2, 5, "LOCATION")]


def test_right_fold_does_not_cross_a_script_break() -> None:
    text = "Ab请cd"
    inputs = [_span(0, 3, "PERSON_NAME"), _span(2, 5, "PHONE_NUMBER", 0.9)]
    out = deduplicate_spans(inputs, {"PHONE_NUMBER"}, text=text)

    assert _triples(out) == [(0, 2, "PERSON_NAME"), (2, 5, "PHONE_NUMBER")]


def test_several_winners_sharing_a_boundary_first_in_order_takes_the_tail() -> None:
    text = "abcdef"
    first = _span(0, 3, "ORGANIZATION_NAME", 0.9)
    second = _span(0, 3, "ORGANIZATION_NAME", 0.5)
    location = _span(2, 6, "LOCATION")

    out = absorb_overlapping_spans([first, second, location], text=text)
    assert [(s.start, s.end, s.score) for s in out] == [(0, 6, 0.9), (0, 3, 0.5)]

    out_reversed = absorb_overlapping_spans([second, first, location], text=text)
    assert [(s.start, s.end, s.score) for s in out_reversed] == [(0, 6, 0.5), (0, 3, 0.9)]


def test_grown_outer_start_is_the_cut_for_a_later_inner_span() -> None:
    text = "abcdefgh"
    org = _span(5, 8, "ORGANIZATION_NAME")
    first_location = _span(2, 6, "LOCATION")
    second_location = _span(0, 3, "LOCATION")

    out = absorb_overlapping_spans([org, first_location, second_location], text=text)

    assert _triples(out) == [(0, 8, "ORGANIZATION_NAME")]
    _assert_no_coverage_lost(text, [org, first_location, second_location], out)


def test_same_type_overlap_with_a_different_type_is_not_merged() -> None:
    text = "ab cdefgh"
    high = {"PHONE_NUMBER"}
    person_left = _span(0, 2, "PERSON_NAME")
    phone = _span(4, 7, "PHONE_NUMBER", 0.9)
    person_right = _span(1, 5, "PERSON_NAME")
    out = deduplicate_spans([person_left, phone, person_right], high, text=text)

    assert _triples(out) == [(0, 2, "PERSON_NAME"), (3, 7, "PHONE_NUMBER")]
    _assert_no_coverage_lost(text, [person_left, phone, person_right], out)


@pytest.mark.parametrize(
    ("stronger", "weaker"),
    [
        pytest.param("SOCIAL_SECURITY_NUMBER", "TAX_ID", id="ssn-over-tax-id"),
        pytest.param("TAX_ID", "DRIVERS_LICENSE", id="tax-id-over-drivers-license"),
        pytest.param("LICENSE_PLATE", "PHONE_NUMBER", id="license-plate-over-phone"),
    ],
)
def test_entity_type_priority_decides_exact_offset_ties(
    stronger: str, weaker: str
) -> None:
    text = "abcdefghij"
    for order in ([stronger, weaker], [weaker, stronger]):
        inputs = [_span(0, 10, entity, 0.8) for entity in order]
        out = deduplicate_spans(inputs, set(), text=text)

        assert _triples(out) == [(0, 10, stronger)]


def test_apostrophe_before_an_unspaced_glyph_is_a_script_break() -> None:
    text = "Ab’请cd"
    inputs = [_span(0, 2, "PERSON_NAME"), _span(1, 5, "LOCATION")]
    out = deduplicate_spans(inputs, HIGH, text=text)

    assert _triples(out) == [(0, 2, "PERSON_NAME"), (3, 5, "LOCATION")]


def test_absorb_clamps_out_of_range_spans_and_discards_empty_ones() -> None:
    text = "abcdef"
    inputs = [
        _span(-5, 3, "ORGANIZATION_NAME"),
        _span(4, 50, "LOCATION"),
        _span(9, 12, "LOCATION"),
        _span(2, 2, "LOCATION"),
    ]
    out = absorb_overlapping_spans(inputs, text=text)

    assert sorted(_triples(out)) == [(0, 3, "ORGANIZATION_NAME"), (4, 6, "LOCATION")]


def test_absorb_discards_empty_and_reversed_spans_without_text() -> None:
    inputs = [_span(5, 5, "LOCATION"), _span(7, 2, "LOCATION")]

    assert absorb_overlapping_spans(inputs) == []


def test_postal_trims_a_street_before_the_street_claims_its_location() -> None:
    text = "bcc b "
    inputs = [
        _span(0, 4, "LOCATION"),
        _span(3, 6, "STREET_ADDRESS"),
        _span(4, 6, "POSTAL_ADDRESS"),
    ]
    out = absorb_overlapping_spans(inputs, text=text)

    assert sorted(_triples(out)) == [(0, 4, "LOCATION"), (4, 6, "POSTAL_ADDRESS")]


def test_remainder_cut_before_a_later_fold_does_not_overlap_the_outer() -> None:
    text = "'acb'c"
    inputs = [
        _span(4, 6, "POSTAL_ADDRESS"),
        _span(0, 3, "LOCATION"),
        _span(0, 6, "STREET_ADDRESS"),
    ]
    out = absorb_overlapping_spans(inputs, text=text)

    assert _triples(out) == [(1, 6, "POSTAL_ADDRESS")]
    _assert_disjoint(out)


FOLDED_NAME_TEXT = "Ali Kaya’nın Ahmet ile toplantısı var."


def _folded_name() -> DetectedSpan:
    """"Ali Kaya" with the folded "’nın" tail: the span ends at 12, the core at 8."""
    return DetectedSpan(start=0, end=12, entity_type="PERSON_NAME", score=0.9, core=(0, 8))


def test_expansion_does_not_step_over_a_folded_suffix_into_the_next_name() -> None:
    spans = [_folded_name(), _span(13, 18, "PERSON_NAME")]
    expanded = expand_person_name_spans(FOLDED_NAME_TEXT, spans)

    assert [(s.start, s.end) for s in expanded] == [(0, 12), (13, 18)]
    assert expanded[0].core == (0, 8)


def test_merge_does_not_join_a_next_name_past_a_folded_suffix() -> None:
    spans = [_folded_name(), _span(13, 18, "PERSON_NAME")]
    merged = merge_adjacent_person_name_spans(FOLDED_NAME_TEXT, spans)

    assert [(s.start, s.end) for s in merged] == [(0, 12), (13, 18)]
    assert merged[0].core == (0, 8)


def test_merge_keeps_two_people_under_two_placeholders_after_a_folded_suffix() -> None:
    text = "Ali Kaya’nın Ahmet Demir ile toplantısı var."
    spans = [_folded_name(), _span(13, 23, "PERSON_NAME")]
    merged = merge_adjacent_person_name_spans(text, expand_person_name_spans(text, spans))

    assert [(s.start, s.end, s.core) for s in merged] == [
        (0, 12, (0, 8)),
        (13, 23, None),
    ]


@pytest.mark.parametrize("apostrophe", ["'", "’"])
def test_digit_run_after_an_apostrophe_is_not_folded_into_the_winner(apostrophe: str) -> None:
    text = f"Ayşe Kaya{apostrophe}1234 evi"
    person = _span(0, 9, "PERSON_NAME", 0.9)
    location = _span(5, 14, "LOCATION", 0.7)
    out = deduplicate_spans([person, location], HIGH, text=text)

    assert _triples(out) == [(0, 9, "PERSON_NAME"), (10, 14, "LOCATION")]
    _assert_disjoint(out)


def _registered(text: str, spans: list[DetectedSpan], language: str = "en") -> dict[str, str]:
    """Originals the anonymization map registers for ``spans`` after deduplication."""
    anon_map = AnonymizationMap(document_id=1, language=language)
    Detector._apply_replacements(text, spans, anon_map, language)
    return anon_map.entity_map


def test_same_type_merge_keeps_the_union_of_the_cores() -> None:
    text = "aaa bbb ccc ddd eee"
    inputs = [
        DetectedSpan(0, 7, "PERSON_NAME", 0.9, core=(0, 3)),
        DetectedSpan(4, 11, "PERSON_NAME", 0.8, core=(4, 7)),
    ]
    out = deduplicate_spans(inputs, HIGH, text=text)

    assert [(s.start, s.end, s.core) for s in out] == [(0, 11, (0, 7))]
    assert _registered(text, out) == {"aaa bbb": "[PERSON_NAME_1]"}


def test_rank_win_keeps_the_winner_core_and_the_loser_remainder() -> None:
    text = "aaa bbb ccc ddd eee"
    inputs = [
        DetectedSpan(0, 7, "PERSON_NAME", 0.9),
        DetectedSpan(4, 11, "EMAIL_ADDRESS", 0.8, core=(8, 11)),
    ]
    out = deduplicate_spans(inputs, HIGH, text=text)

    assert [(s.start, s.end, s.entity_type, s.core) for s in out] == [
        (0, 3, "PERSON_NAME", None),
        (4, 11, "EMAIL_ADDRESS", (8, 11)),
    ]
    assert _registered(text, out) == {"aaa": "[PERSON_NAME_1]", "ccc": "[EMAIL_ADDRESS_1]"}


def test_loser_remainder_folded_into_the_winner_keeps_the_winner_core() -> None:
    text = "Kaya’nın x"
    inputs = [
        DetectedSpan(0, 8, "PERSON_NAME", 0.9),
        DetectedSpan(0, 4, "EMAIL_ADDRESS", 0.8),
    ]
    out = deduplicate_spans(inputs, HIGH, text=text)

    assert [(s.start, s.end, s.entity_type, s.core) for s in out] == [
        (0, 8, "EMAIL_ADDRESS", (0, 4)),
    ]
    assert _registered(text, out) == {"Kaya": "[EMAIL_ADDRESS_1]"}


def test_remainder_path_registers_only_the_core_of_the_trimmed_piece() -> None:
    text = "aaaa bbb ccc"
    inputs = [
        DetectedSpan(0, 11, "PERSON_NAME", 0.9, core=(0, 8)),
        DetectedSpan(0, 4, "PHONE_NUMBER", 0.8),
    ]
    out = deduplicate_spans(inputs, HIGH, text=text)

    assert [(s.start, s.end, s.entity_type, s.core) for s in out] == [
        (0, 4, "PHONE_NUMBER", None),
        (5, 11, "PERSON_NAME", (5, 8)),
    ]
    assert _registered(text, out) == {"aaaa": "[PHONE_NUMBER_1]", "bbb": "[PERSON_NAME_1]"}


def test_cross_script_apostrophe_does_not_fold_a_tail() -> None:
    text = "ก′ا ตก"
    inputs = [_span(0, 3, "PERSON_NAME"), _span(0, 1, "EMAIL_ADDRESS")]
    out = deduplicate_spans(inputs, set(), text=text)

    assert _triples(out) == [(0, 1, "EMAIL_ADDRESS"), (2, 3, "PERSON_NAME")]


def test_touching_absorb_spans_are_not_swallowed() -> None:
    text = "xxxxx yyy"
    inputs = [_span(0, 5, "ORGANIZATION_NAME"), _span(5, 9, "LOCATION")]
    out = absorb_overlapping_spans(inputs, text=text)

    assert _triples(out) == [(0, 5, "ORGANIZATION_NAME"), (5, 9, "LOCATION")]


def test_digit_after_apostrophe_is_not_folded_into_the_winner() -> None:
    text = "Kaya’1x"
    inputs = [_span(0, 4, "EMAIL_ADDRESS"), _span(0, 6, "PERSON_NAME")]
    out = deduplicate_spans(inputs, set(), text=text)

    assert _triples(out) == [(0, 4, "EMAIL_ADDRESS"), (5, 6, "PERSON_NAME")]


def test_combining_mark_after_apostrophe_is_folded_into_the_winner() -> None:
    text = "Kaya’́n x"
    inputs = [_span(0, 4, "EMAIL_ADDRESS"), _span(0, 6, "PERSON_NAME")]
    out = deduplicate_spans(inputs, set(), text=text)

    assert [(s.start, s.end, s.core) for s in out] == [(0, 6, (0, 4))]


def test_tail_starts_with_letter_is_total_at_the_text_edges() -> None:
    assert not _tail_starts_with_letter("Kaya’", 4)
    assert not _tail_starts_with_letter("Kaya’", 5)
    assert not _tail_starts_with_letter("Kaya’1", 4)
    assert _tail_starts_with_letter("Kaya’nın", 4)
    assert _tail_starts_with_letter("Kaya’́n", 4)


def test_degenerate_core_registers_the_whole_span() -> None:
    anon_map = AnonymizationMap(document_id=1, language="en")

    Detector._apply_replacements(
        "John Smith x", [DetectedSpan(0, 10, "PERSON_NAME", 0.9, core=(3, 3))], anon_map, "en"
    )

    assert anon_map.entity_map == {"John Smith": "[PERSON_NAME_1]"}


def test_whitespace_only_core_registers_the_whole_span() -> None:
    anon_map = AnonymizationMap(document_id=1, language="en")

    Detector._apply_replacements(
        "John Smith x", [DetectedSpan(0, 10, "PERSON_NAME", 0.9, core=(4, 5))], anon_map, "en"
    )

    assert anon_map.entity_map == {"John Smith": "[PERSON_NAME_1]"}


def test_core_outside_its_span_registers_the_whole_span() -> None:
    anon_map = AnonymizationMap(document_id=1, language="en")

    Detector._apply_replacements(
        "John Smith x", [DetectedSpan(0, 4, "PERSON_NAME", 0.9, core=(6, 9))], anon_map, "en"
    )

    assert anon_map.entity_map == {"John": "[PERSON_NAME_1]"}


def test_core_is_registered_not_the_whole_span() -> None:
    anon_map = AnonymizationMap(document_id=1, language="en")

    Detector._apply_replacements(
        "xElm", [DetectedSpan(2, 4, "PHONE_NUMBER", 0.9, core=(3, 4))], anon_map, "en"
    )

    assert anon_map.entity_map == {"m": "[PHONE_NUMBER_1]"}


def test_person_span_with_a_suffix_tail_does_not_merge_into_the_next_name() -> None:
    text = "Ali Kaya’nın Demir"
    assert text[0:12] == "Ali Kaya’nın" and text[13:18] == "Demir"
    spans = [
        DetectedSpan(0, 12, "PERSON_NAME", 0.9, core=(0, 8)),
        DetectedSpan(13, 18, "PERSON_NAME", 0.9),
    ]

    merged = merge_adjacent_person_name_spans(text, spans)
    expanded = expand_person_name_spans(text, spans)

    assert [(s.start, s.end, s.core) for s in merged] == [(0, 12, (0, 8)), (13, 18, None)]
    assert [(s.start, s.end, s.core) for s in expanded] == [(0, 12, (0, 8)), (13, 18, None)]
    assert _registered(text, merged, "tr") == {"Ali Kaya": "[PERSON_NAME_1]", "Demir": "[PERSON_NAME_2]"}
