"""A labelled EMPLOYEE_ID code absorbs the identifier spans found inside it.

The code is reported only after a label cue and only as a whole token, so a
national id or phone pattern matching a digit run inside it is part of that
one code. Cut around them, the code left a one-letter EMPLOYEE_ID remainder
("T") that the map then matched all over the document.
"""

from __future__ import annotations

import pytest
from septum_core import SeptumEngine
from septum_core.config import SeptumCoreConfig
from septum_core.detector import _HIGH_PRIORITY_ENTITY_TYPES
from septum_core.span_processing import (
    _ABSORPTION_RULES,
    absorb_overlapping_spans,
    deduplicate_spans,
)
from septum_core.spans import DetectedSpan

_PRESIDIO_ONLY = SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=False)
_CODE_TEXT = "No: T-047382910456-0001"


_CODE = (4, 23)


def test_labelled_code_is_one_entity_not_split_around_inner_digit_runs() -> None:
    engine = SeptumEngine(regulations=["popia"], config=_PRESIDIO_ONLY)

    result = engine.mask("Sicil No: T-047382910456-0001", language="tr")

    assert result.masked_text == "Sicil No: [EMPLOYEE_ID_1]"
    assert [s.entity_type for s in result.detected_spans] == ["EMPLOYEE_ID"]


def test_employee_id_absorbs_inner_identifier_spans() -> None:
    text = "No: T-047382910456-0001"
    spans = [
        DetectedSpan(start=4, end=23, entity_type="EMPLOYEE_ID", score=0.75),
        DetectedSpan(start=6, end=18, entity_type="NATIONAL_ID", score=0.6),
        DetectedSpan(start=6, end=23, entity_type="PHONE_NUMBER", score=0.4),
    ]

    out = deduplicate_spans(
        absorb_overlapping_spans(spans, text=text), _HIGH_PRIORITY_ENTITY_TYPES, text=text
    )

    assert [(s.start, s.end, s.entity_type) for s in out] == [(4, 23, "EMPLOYEE_ID")]


@pytest.mark.parametrize("inner_type", sorted(_ABSORPTION_RULES["EMPLOYEE_ID"]))
def test_employee_id_absorbs_every_listed_inner_type(inner_type: str) -> None:
    spans = [
        DetectedSpan(start=_CODE[0], end=_CODE[1], entity_type="EMPLOYEE_ID", score=0.75),
        DetectedSpan(start=6, end=18, entity_type=inner_type, score=0.9),
    ]

    out = absorb_overlapping_spans(spans, text=_CODE_TEXT)

    assert [(s.start, s.end, s.entity_type) for s in out] == [(4, 23, "EMPLOYEE_ID")]


def test_employee_id_does_not_absorb_an_email_inside_it() -> None:
    spans = [
        DetectedSpan(start=_CODE[0], end=_CODE[1], entity_type="EMPLOYEE_ID", score=0.75),
        DetectedSpan(start=6, end=18, entity_type="EMAIL_ADDRESS", score=1.0),
    ]

    out = absorb_overlapping_spans(spans, text=_CODE_TEXT)

    assert sorted(s.entity_type for s in out) == ["EMAIL_ADDRESS", "EMPLOYEE_ID"]


def test_phone_reaching_past_the_code_keeps_its_remainder() -> None:
    text = "No: T-047382910456-0001 555 1234"
    spans = [
        DetectedSpan(start=4, end=23, entity_type="EMPLOYEE_ID", score=0.75),
        DetectedSpan(start=19, end=32, entity_type="PHONE_NUMBER", score=0.5),
    ]

    out = absorb_overlapping_spans(spans, text=text)

    assert [(s.start, s.end, s.entity_type) for s in out] == [
        (4, 23, "EMPLOYEE_ID"),
        (24, 32, "PHONE_NUMBER"),
    ]


def test_without_employee_id_in_the_policy_the_digit_runs_still_mask() -> None:
    engine = SeptumEngine(regulations=["kvkk"], config=_PRESIDIO_ONLY)

    result = engine.mask("Sicil No: T-047382910456-0001", language="tr")

    assert "EMPLOYEE_ID" not in {s.entity_type for s in result.detected_spans}
    assert "047382910456" not in result.masked_text
