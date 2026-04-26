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


def test_dedup_pipeline_with_absorption_yields_org_only() -> None:
    """End-to-end: absorb + dedup leaves ORG when it overlaps a LOC at any offset."""
    loc = DetectedSpan(start=0, end=8, entity_type="LOCATION", score=0.95)
    org = DetectedSpan(start=2, end=25, entity_type="ORGANIZATION_NAME", score=0.9)
    pre = absorb_overlapping_spans([loc, org])
    final = deduplicate_spans(pre, high_priority_types=set())
    assert [s.entity_type for s in final] == ["ORGANIZATION_NAME"]
