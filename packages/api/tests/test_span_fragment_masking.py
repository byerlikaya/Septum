"""End-to-end guard: a span remainder must not corrupt chat-time masking.

Two NER layers can disagree on a token boundary: a PERSON span covers
"Ayşe Kaya" while a LOCATION span covers "Kaya'nın". The leftover "nın" must
not become its own anonymization-map entry, because chat-time masking matches
map originals as plain substrings and would turn "binanın" into a placeholder.
Spans go through the same core steps as ``Detector.sanitize``; the anonymization
map is then checked with the API's ``mask_known_entities``.
"""

from __future__ import annotations

from septum_api.services import pii_gate
from septum_api.services.pii_gate import mask_known_entities
from septum_core.anonymization_map import AnonymizationMap
from septum_core.detector import _HIGH_PRIORITY_ENTITY_TYPES, Detector
from septum_core.span_processing import (
    absorb_overlapping_spans,
    deduplicate_spans,
    expand_person_name_spans,
    merge_adjacent_person_name_spans,
)
from septum_core.spans import DetectedSpan, ResolvedSpan, SanitizeResult
from septum_core.unmasker import Unmasker


def _ingest(
    text: str, spans: list[DetectedSpan], language: str
) -> tuple[str, AnonymizationMap]:
    absorbed = absorb_overlapping_spans(spans, text=text)
    resolved = deduplicate_spans(absorbed, _HIGH_PRIORITY_ENTITY_TYPES, text=text)
    expanded = expand_person_name_spans(text, resolved)
    merged = merge_adjacent_person_name_spans(text, expanded)
    anon_map = AnonymizationMap(document_id=1, language=language)
    masked, _, _ = Detector._apply_replacements(text, merged, anon_map, language)
    return anon_map.apply_blocklist(masked, language), anon_map


def test_location_fragment_of_a_name_does_not_corrupt_later_words() -> None:
    text = "Ayşe Kaya'nın evi okulun içinde, binanın önünde."
    kaya = text.index("Kaya")
    spans = [
        DetectedSpan(start=0, end=9, entity_type="PERSON_NAME", score=0.9),
        DetectedSpan(
            start=kaya, end=kaya + len("Kaya'nın"), entity_type="LOCATION", score=0.7
        ),
    ]

    masked, anon_map = _ingest(text, spans, "tr")

    assert masked == "[PERSON_NAME_1] evi okulun içinde, binanın önünde."
    assert "nın" not in anon_map.entity_map
    assert mask_known_entities(masked, anon_map) == masked


def test_trailing_period_is_not_a_location_original() -> None:
    text = "Antalya Sağlık Merkezi. Randevu 10.00 da."
    spans = [
        DetectedSpan(start=0, end=22, entity_type="ORGANIZATION_NAME", score=0.8),
        DetectedSpan(start=0, end=23, entity_type="LOCATION", score=0.7),
    ]

    masked, anon_map = _ingest(text, spans, "tr")

    assert masked == "[ORGANIZATION_NAME_1]. Randevu 10.00 da."
    assert "." not in anon_map.entity_map
    assert mask_known_entities(masked, anon_map) == masked


def test_folded_turkish_suffix_registers_the_name_and_masks_later_bare_surname() -> None:
    text = "Ayşe Kaya’nın evi okulun yanında. Kaya her sabah 8'de çıkar. Ayşe'ye sorduk."
    kaya = text.index("Kaya")
    spans = [
        DetectedSpan(start=0, end=9, entity_type="PERSON_NAME", score=0.9),
        DetectedSpan(start=kaya, end=kaya + len("Kaya’nın"), entity_type="LOCATION", score=0.7),
    ]

    masked, anon_map = _ingest(text, spans, "tr")

    assert masked == (
        "[PERSON_NAME_1] evi okulun yanında. [PERSON_NAME_1] her sabah 8'de çıkar. "
        "[PERSON_NAME_1]'ye sorduk."
    )
    assert anon_map.entity_map == {"Ayşe Kaya": "[PERSON_NAME_1]"}
    assert mask_known_entities("Kaya’nın adresi", anon_map) == "[PERSON_NAME_1]’nın adresi"
    assert Unmasker().unmask("[PERSON_NAME_1]’nın evi", anon_map) == "Ayşe Kaya’nın evi"


def test_folded_english_possessive_registers_the_name_and_masks_later_surname() -> None:
    for apostrophe in ("'", "’"):
        text = f"John Smith{apostrophe}s office is in Leeds. Smith arrives at 9."
        spans = [
            DetectedSpan(start=0, end=10, entity_type="PERSON_NAME", score=0.9),
            DetectedSpan(start=5, end=19, entity_type="LOCATION", score=0.7),
        ]

        masked, anon_map = _ingest(text, spans, "en")

        assert masked == "[PERSON_NAME_1] [LOCATION_1] is in Leeds. [PERSON_NAME_1] arrives at 9."
        assert anon_map.entity_map == {
            "John Smith": "[PERSON_NAME_1]",
            "office": "[LOCATION_1]",
        }
        assert mask_known_entities("Where is Smith?", anon_map) == "Where is [PERSON_NAME_1]?"
        assert Unmasker().unmask(f"[PERSON_NAME_1]{apostrophe}s", anon_map) == (
            f"John Smith{apostrophe}s"
        )


def test_folded_genitive_without_apostrophe_registers_the_name() -> None:
    text = "Thomas Müllers Wohnung liegt in Berlin. Müller zahlt die Miete."
    spans = [
        DetectedSpan(start=0, end=13, entity_type="PERSON_NAME", score=0.9),
        DetectedSpan(start=7, end=22, entity_type="LOCATION", score=0.7),
    ]

    masked, anon_map = _ingest(text, spans, "de")

    assert masked == "[PERSON_NAME_1] [LOCATION_1] liegt in Berlin. [PERSON_NAME_1] zahlt die Miete."
    assert anon_map.entity_map == {
        "Thomas Müller": "[PERSON_NAME_1]",
        "Wohnung": "[LOCATION_1]",
    }
    assert mask_known_entities("Wann zahlt Müller?", anon_map) == (
        "Wann zahlt [PERSON_NAME_1]?"
    )
    assert Unmasker().unmask(masked, anon_map).startswith("Thomas Müller ")
    assert Unmasker().unmask("[PERSON_NAME_1]s", anon_map) == "Thomas Müllers"


def test_street_trimmed_by_postal_address_still_absorbs_its_location_tail() -> None:
    text = "Atatürk Caddesi'nde oturuyor. Posta: 34000 Kadıköy. Binanın önünde bekle."
    street = DetectedSpan(start=0, end=15, entity_type="STREET_ADDRESS", score=0.8)
    location = DetectedSpan(start=8, end=19, entity_type="LOCATION", score=0.7)
    postal_start = text.index("34000")
    postal = DetectedSpan(
        start=postal_start,
        end=postal_start + len("34000 Kadıköy"),
        entity_type="POSTAL_ADDRESS",
        score=0.8,
    )

    masked, anon_map = _ingest(text, [street, location, postal], "tr")

    assert masked == "[STREET_ADDRESS_1] oturuyor. Posta: [POSTAL_ADDRESS_1]. Binanın önünde bekle."
    assert "nde" not in anon_map.entity_map
    assert mask_known_entities(masked, anon_map) == masked


def test_street_without_postal_address_absorbs_the_location_tail() -> None:
    text = "Atatürk Caddesi'nde oturuyor."
    street = DetectedSpan(start=0, end=15, entity_type="STREET_ADDRESS", score=0.8)
    location = DetectedSpan(start=8, end=19, entity_type="LOCATION", score=0.7)

    masked, anon_map = _ingest(text, [street, location], "tr")

    assert masked == "[STREET_ADDRESS_1] oturuyor."
    assert anon_map.entity_map == {"Atatürk Caddesi": "[STREET_ADDRESS_1]"}


class _FixedSpanSanitizer:
    """Reports the same resolved spans for every text, with their ``core`` as given."""

    def __init__(self, *spans: ResolvedSpan) -> None:
        self._spans = list(spans)

    def sanitize(self, text: str, language: str, anon_map: AnonymizationMap) -> SanitizeResult:
        return SanitizeResult(
            sanitized_text=text, entity_count=len(self._spans), detected_spans=list(self._spans)
        )


def test_gate_registers_the_core_of_a_suffixed_span_not_its_tail() -> None:
    text = "Ali Kaya’nın evi"
    anon_map = AnonymizationMap(document_id=1, language="tr")
    sanitizer = _FixedSpanSanitizer(
        ResolvedSpan(0, 12, "PERSON_NAME", "", 0.9, core=(0, 8)),
    )

    masked = pii_gate._gate_one(text, anon_map, sanitizer, "tr")

    assert masked == "[PERSON_NAME_1] evi"
    assert anon_map.entity_map == {"Ali Kaya": "[PERSON_NAME_1]"}


def test_gate_registers_the_whole_span_when_the_core_is_degenerate() -> None:
    text = "John Smith x"
    anon_map = AnonymizationMap(document_id=1, language="en")
    sanitizer = _FixedSpanSanitizer(
        ResolvedSpan(0, 10, "PERSON_NAME", "", 0.9, core=(3, 3)),
    )

    masked = pii_gate._gate_one(text, anon_map, sanitizer, "en")

    assert masked == "[PERSON_NAME_1] x"
    assert anon_map.entity_map == {"John Smith": "[PERSON_NAME_1]"}
