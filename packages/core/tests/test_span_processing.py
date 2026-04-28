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


def test_org_absorbs_partial_overlap_location_with_lower_start() -> None:
    """The pre-existing dedup bug: LOC starts before ORG yet partially overlaps it.

    Plain length-based dedup picks LOC because it sorts first; the
    domain-aware filter must drop LOC and let ORG survive.
    """
    loc = DetectedSpan(start=0, end=8, entity_type="LOCATION", score=0.95)
    org = DetectedSpan(start=2, end=25, entity_type="ORGANIZATION_NAME", score=0.9)
    result = absorb_overlapping_spans([loc, org])
    assert [s.entity_type for s in result] == ["ORGANIZATION_NAME"]


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


def test_dedup_pipeline_with_absorption_yields_org_only() -> None:
    """End-to-end: absorb + dedup leaves ORG when it overlaps a LOC at any offset."""
    loc = DetectedSpan(start=0, end=8, entity_type="LOCATION", score=0.95)
    org = DetectedSpan(start=2, end=25, entity_type="ORGANIZATION_NAME", score=0.9)
    pre = absorb_overlapping_spans([loc, org])
    final = deduplicate_spans(pre, high_priority_types=set())
    assert [s.entity_type for s in final] == ["ORGANIZATION_NAME"]
