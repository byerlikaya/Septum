from __future__ import annotations

from septum_core.span_processing import (
    absorb_overlapping_spans,
    deduplicate_spans,
)
from septum_core.spans import DetectedSpan


def test_org_absorbs_contained_location() -> None:
    """LOCATION fully inside ORG must be dropped, ORG kept."""
    org = DetectedSpan(start=0, end=22, entity_type="ORGANIZATION_NAME", score=0.9)
    loc = DetectedSpan(start=0, end=7, entity_type="LOCATION", score=0.95)
    result = absorb_overlapping_spans([loc, org])
    assert [s.entity_type for s in result] == ["ORGANIZATION_NAME"]


def test_org_partial_overlap_trims_location_and_keeps_its_tail() -> None:
    """LOC partially overlaps ORG: the overlapped part is cut, the tail stays masked.

    Dropping the whole LOC span would leave ``[21, 34)`` raw; the ORG span
    stays whole.
    """
    org = DetectedSpan(start=0, end=21, entity_type="ORGANIZATION_NAME", score=0.9)
    loc = DetectedSpan(start=13, end=34, entity_type="LOCATION", score=0.95)
    result = absorb_overlapping_spans([loc, org])
    assert sorted((s.start, s.end, s.entity_type) for s in result) == [
        (0, 21, "ORGANIZATION_NAME"),
        (21, 34, "LOCATION"),
    ]


def test_date_partial_overlap_with_date_of_birth_keeps_date_tail() -> None:
    dob = DetectedSpan(start=0, end=10, entity_type="DATE_OF_BIRTH", score=0.9)
    date = DetectedSpan(start=5, end=15, entity_type="DATE", score=0.8)
    result = absorb_overlapping_spans([date, dob])
    assert sorted((s.start, s.end, s.entity_type) for s in result) == [
        (0, 10, "DATE_OF_BIRTH"),
        (10, 15, "DATE"),
    ]


def test_non_overlapping_org_and_location_both_kept() -> None:
    """Distinct LOCATION and ORG spans are independent and must coexist."""
    loc = DetectedSpan(start=0, end=7, entity_type="LOCATION", score=0.95)
    org = DetectedSpan(start=20, end=42, entity_type="ORGANIZATION_NAME", score=0.9)
    result = absorb_overlapping_spans([loc, org])
    types = sorted(s.entity_type for s in result)
    assert types == ["LOCATION", "ORGANIZATION_NAME"]


def test_person_name_expansion_does_not_cross_newline() -> None:
    """Form-style label/value layouts must not absorb the label as a name token.

    PDF/extracted text often arranges fields as ``Label\\nValue\\n``
    (KVKK consent forms, hospital intake forms, employment contracts).
    The expand pass used to skip newlines while looking for a left
    surname token and gobbled the label ("Ad Soyad" became part of
    "Fatma Nur Öztürk"), which then poisoned the entity_index hash
    and broke chat-time entity lookups.
    """
    from septum_core.span_processing import expand_person_name_spans

    text = "Ad Soyad\nFatma Nur Öztürk\nT.C. Kimlik No\n12345678901"
    name_start = text.index("Fatma")
    name_end = name_start + len("Fatma Nur Öztürk")
    spans = [
        DetectedSpan(
            start=name_start,
            end=name_end,
            entity_type="PERSON_NAME",
            score=0.97,
        )
    ]
    expanded = expand_person_name_spans(text, spans)
    assert len(expanded) == 1
    assert expanded[0].start == name_start
    assert expanded[0].end == name_end
    assert text[expanded[0].start : expanded[0].end] == "Fatma Nur Öztürk"


def test_person_name_expansion_still_expands_inline_tokens() -> None:
    """Inline expansion (no newline) must keep working — only newlines abort."""
    from septum_core.span_processing import expand_person_name_spans

    text = "John Smith works here."
    spans = [
        DetectedSpan(
            start=0,
            end=4,  # "John"
            entity_type="PERSON_NAME",
            score=0.95,
        )
    ]
    expanded = expand_person_name_spans(text, spans)
    assert text[expanded[0].start : expanded[0].end] == "John Smith"


def test_dedup_pipeline_with_absorption_keeps_org_and_masks_location_tail() -> None:
    """End-to-end: absorb + dedup keeps ORG intact and still masks the LOC prefix."""
    loc = DetectedSpan(start=0, end=8, entity_type="LOCATION", score=0.95)
    org = DetectedSpan(start=2, end=25, entity_type="ORGANIZATION_NAME", score=0.9)
    pre = absorb_overlapping_spans([loc, org])
    final = deduplicate_spans(pre, high_priority_types=set())
    assert [(s.start, s.end, s.entity_type) for s in final] == [
        (0, 2, "LOCATION"),
        (2, 25, "ORGANIZATION_NAME"),
    ]
