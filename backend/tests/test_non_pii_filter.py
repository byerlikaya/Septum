from __future__ import annotations

"""Tests for the Non-PII decision layer."""

from datetime import datetime, timezone

from backend.app.models.regulation import NonPiiRule
from backend.app.services.non_pii_filter import NonPiiFilter, SpanView


def test_non_pii_filter_removes_configured_token() -> None:
    rule = NonPiiRule(
        id=1,
        pattern_type="token",
        pattern="merhaba",
        languages=["tr"],
        entity_types=["PERSON_NAME"],
        min_score=None,
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    flt = NonPiiFilter.from_rules([rule])
    assert flt is not None

    text = "Merhaba"
    spans = [
        SpanView(start=0, end=len(text), entity_type="PERSON_NAME", score=0.9),
    ]
    kept = flt.filter_spans(text=text, language="tr", spans=spans)
    # Span should be removed as non-PII.
    assert kept == []


def test_non_pii_filter_keeps_other_entities() -> None:
    rule = NonPiiRule(
        id=1,
        pattern_type="token",
        pattern="merhaba",
        languages=["tr"],
        entity_types=["PERSON_NAME"],
        min_score=None,
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    flt = NonPiiFilter.from_rules([rule])
    assert flt is not None

    text = "Barış"
    spans = [
        SpanView(start=0, end=len(text), entity_type="PERSON_NAME", score=0.9),
    ]
    kept = flt.filter_spans(text=text, language="tr", spans=spans)
    # Rule matches only \"merhaba\"; this span must be preserved.
    assert kept == spans

