"""The contextual layer runs on its own setting and a failure refuses instead of passing.

``use_semantic_contextual_detection`` alone gates the layer: no dependence on the
alias layer setting, no word floor. Alias detection and coreference stay off
without ``use_semantic_detection``. A raising adapter must propagate.
"""

from __future__ import annotations

from typing import List

import pytest
from septum_core import SeptumCoreConfig, SeptumEngine
from septum_core.detector import Detector
from septum_core.spans import DetectedSpan

LONG_TEXT = (
    "During the follow-up visit the patient said that Type 2 diabetes was "
    "diagnosed last spring and that the family is coping well."
)
SHORT_TEXT = "Has Type 2 diabetes"


class _RecordingPort:
    """A semantic adapter that records its calls and finds the value ``Type 2 diabetes``."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def is_enabled(self) -> bool:
        return True

    def validate_candidates(self, *, text, candidate_spans, language, regulation_context):
        self.calls.append("validate_candidates")
        return list(candidate_spans)

    def detect_aliases(self, *, normalized_text) -> List[DetectedSpan]:
        self.calls.append("detect_aliases")
        return []

    def detect_semantic(self, *, normalized_text, entity_types) -> List[DetectedSpan]:
        self.calls.append("detect_semantic")
        start = normalized_text.find("Type 2 diabetes")
        if start < 0:
            return []
        return [DetectedSpan(start, start + len("Type 2 diabetes"), "DIAGNOSIS", 0.8)]

    def resolve_coreference(self, *, normalized_text, anon_map, language):
        self.calls.append("resolve_coreference")
        return 0


class _RaisingPort(_RecordingPort):
    def detect_semantic(self, *, normalized_text, entity_types) -> List[DetectedSpan]:
        self.calls.append("detect_semantic")
        raise RuntimeError("local model unavailable")


def _config() -> SeptumCoreConfig:
    return SeptumCoreConfig(
        use_presidio_layer=True,
        use_ner_layer=False,
        use_semantic_validation=False,
        use_semantic_detection=False,
        use_semantic_contextual_detection=True,
    )


def _engine(port: _RecordingPort) -> SeptumEngine:
    return SeptumEngine(regulations=["hipaa"], config=_config(), semantic_port=port)


def test_contextual_layer_runs_without_the_alias_layer_setting() -> None:
    port = _RecordingPort()

    result = _engine(port).mask(LONG_TEXT, language="en")

    assert "detect_semantic" in port.calls
    assert "detect_aliases" not in port.calls
    assert "resolve_coreference" not in port.calls
    assert "Type 2 diabetes" not in result.masked_text
    assert "[DIAGNOSIS_1]" in result.masked_text


def test_a_failing_contextual_layer_propagates_out_of_detect() -> None:
    port = _RaisingPort()
    policy = SeptumEngine._compose_policy(["hipaa"])
    detector = Detector(config=_config(), policy=policy, semantic_port=port)

    with pytest.raises(RuntimeError, match="local model unavailable"):
        detector.detect(LONG_TEXT, "en")

    assert "detect_semantic" in port.calls


def test_a_failing_contextual_layer_propagates_out_of_mask() -> None:
    port = _RaisingPort()

    with pytest.raises(RuntimeError, match="local model unavailable"):
        _engine(port).mask(LONG_TEXT, language="en")


def test_a_three_word_text_runs_the_contextual_layer() -> None:
    port = _RecordingPort()

    result = _engine(port).mask(SHORT_TEXT, language="en")

    assert "detect_semantic" in port.calls
    assert "[DIAGNOSIS_1]" in result.masked_text
