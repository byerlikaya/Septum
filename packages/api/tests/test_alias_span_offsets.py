"""Alias-layer spans must land on the original text despite a dotted ``İ``.

``"İ".lower()`` is two characters long, so a value located in lowered
text used to shift one position for every ``İ`` before it, leaving the
first letter of the value unmasked.
"""

from __future__ import annotations

import pytest

from septum_api.services.sanitizer import _alias_span, _unique_span_offsets


@pytest.mark.parametrize(
    "text",
    [
        "İzmir'de Ahmet Kaya ile görüştük.",
        "İSTANBUL ve İZMİR şubelerinden Ahmet Kaya katıldı.",
        "Met at İstanbul office: Ahmet Kaya.",
    ],
)
def test_offsets_after_dotted_capital_point_at_the_value(text: str) -> None:
    start, end = _unique_span_offsets(text, "Ahmet Kaya")
    assert text[start:end] == "Ahmet Kaya"


def test_value_containing_dotted_capital_is_found() -> None:
    text = "Rapor: İLKNUR KAYA imzaladı."
    start, end = _unique_span_offsets(text, "İlknur Kaya")
    assert text[start:end] == "İLKNUR KAYA"


def test_repeated_value_is_still_rejected() -> None:
    assert _unique_span_offsets("İzmir: Ahmet Kaya, Ahmet Kaya", "Ahmet Kaya") is None


def test_alias_span_masks_the_whole_value() -> None:
    text = "İzmir'de Ahmet Kaya ile görüştük."
    span = _alias_span({"text": "Ahmet Kaya", "entity_type": "PERSON_NAME"}, text)
    assert span is not None
    assert text[span.start : span.end] == "Ahmet Kaya"
