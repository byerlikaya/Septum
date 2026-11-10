"""The semantic validation layer may not drop a confident candidate, whatever the port answers."""

from __future__ import annotations

from typing import List

from septum_core import SeptumCoreConfig
from septum_core.detector import SEMANTIC_VALIDATION_KEEPS_MIN_SCORE, Detector
from septum_core.spans import DetectedSpan

_CONFIG = SeptumCoreConfig(
    use_presidio_layer=False,
    use_ner_layer=False,
    use_semantic_validation=True,
    use_semantic_detection=False,
    use_semantic_contextual_detection=False,
)
_TEXT = "Zed Quill met Ann Lee in Rome."


class _AnsweringPort:
    """A validation port that confirms only the candidates it is told to."""

    def __init__(self, confirmed: List[DetectedSpan]) -> None:
        self._confirmed = confirmed

    def is_enabled(self) -> bool:
        return True

    def validate_candidates(self, *, text, candidate_spans, language, regulation_context):
        return [s for s in candidate_spans if s in self._confirmed]

    def detect_aliases(self, *, normalized_text):
        return []

    def detect_semantic(self, *, normalized_text, entity_types):
        return []

    def resolve_coreference(self, *, normalized_text, anon_map, language):
        return 0


def _validate(spans: List[DetectedSpan], confirmed: List[DetectedSpan]) -> List[DetectedSpan]:
    detector = Detector(_CONFIG, semantic_port=_AnsweringPort(confirmed))
    return detector._semantic_validate_candidates(_TEXT, spans, "en")


def test_candidates_at_the_floor_survive_a_port_that_drops_them() -> None:
    confident = DetectedSpan(start=0, end=8, entity_type="PERSON_NAME", score=0.99)
    border = DetectedSpan(
        start=13, end=20, entity_type="PERSON_NAME", score=SEMANTIC_VALIDATION_KEEPS_MIN_SCORE
    )
    weak = DetectedSpan(start=24, end=28, entity_type="LOCATION", score=0.6)

    assert _validate([confident, border, weak], [weak]) == [confident, border, weak]


def test_a_weak_candidate_the_port_rejects_is_dropped() -> None:
    confident = DetectedSpan(start=0, end=8, entity_type="PERSON_NAME", score=0.99)
    weak = DetectedSpan(start=24, end=28, entity_type="LOCATION", score=0.6)

    assert _validate([confident, weak], [confident]) == [confident]


def test_a_candidate_just_below_the_floor_is_the_ports_to_drop() -> None:
    below = DetectedSpan(
        start=0, end=8, entity_type="PERSON_NAME", score=SEMANTIC_VALIDATION_KEEPS_MIN_SCORE - 0.01
    )
    kept = DetectedSpan(start=24, end=28, entity_type="LOCATION", score=0.6)

    assert _validate([below, kept], [kept]) == [kept]
