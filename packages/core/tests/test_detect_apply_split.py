"""``detect`` + ``apply`` is exactly ``sanitize``; ``detect`` never touches a map."""

from __future__ import annotations

import copy

import pytest
from septum_core import SeptumCoreConfig, SeptumEngine
from septum_core.anonymization_map import AnonymizationMap
from septum_core.detector import Detector
from septum_core.ports import NullSemanticDetectionPort

_CONFIG = SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=False)

_CASES = (
    ("en", "Write to jane.doe@example.com or call +44 20 7946 0958 about IBAN GB82WEST12345698765432."),
    ("tr", "Ali Yılmaz'ın e-postası ali.yilmaz@ornek.com ve telefonu +90 532 123 45 67 idi."),
    ("de", "Bitte antworten Sie an hans.mueller@beispiel.de oder rufen Sie +49 30 123456 an."),
    ("zh", "请联系 zhang.wei@example.cn，电话 +86 138 0013 8000。"),
    ("en", "Repeat jane.doe@example.com then jane.doe@example.com again, plus bob@example.org."),
    ("en", "Nothing sensitive in this sentence at all."),
    ("en", ""),
)


@pytest.fixture(scope="module")
def detector() -> Detector:
    engine = SeptumEngine(
        regulations=["gdpr", "kvkk"], config=_CONFIG, semantic_port=NullSemanticDetectionPort()
    )
    return engine._detector


def _map(language: str) -> AnonymizationMap:
    return AnonymizationMap(document_id=1, language=language)


def _state(anon_map: AnonymizationMap) -> dict:
    return {k: copy.deepcopy(v) for k, v in vars(anon_map).items() if not k.startswith("_")}


@pytest.mark.parametrize(("language", "text"), _CASES)
def test_detect_then_apply_equals_sanitize(detector, language, text) -> None:
    whole_map, split_map = _map(language), _map(language)

    whole = detector.sanitize(text, language, whole_map)
    split = detector.apply(detector.detect(text, language), split_map)

    assert split == whole
    assert _state(split_map) == _state(whole_map)


def test_two_texts_share_one_map_identically(detector) -> None:
    whole_map, split_map = _map("en"), _map("en")
    texts = [_CASES[0][1], _CASES[4][1]]

    whole = [detector.sanitize(t, "en", whole_map) for t in texts]
    split = [detector.apply(detector.detect(t, "en"), split_map) for t in texts]

    assert split == whole
    assert _state(split_map) == _state(whole_map)


def test_detect_does_not_mutate_a_map_and_returns_spans(detector) -> None:
    anon_map = _map("en")
    detector.sanitize("seed jane.doe@example.com", "en", anon_map)
    before = _state(anon_map)

    detection = detector.detect(_CASES[4][1], "en")

    assert _state(anon_map) == before
    assert detection.spans
    assert all(detection.text[s.start : s.end].strip() for s in detection.spans)


def test_apply_is_deterministic(detector) -> None:
    detection = detector.detect(_CASES[0][1], "en")

    first_map, second_map = _map("en"), _map("en")
    first = detector.apply(detection, first_map)
    second = detector.apply(detection, second_map)

    assert first == second
    assert _state(first_map) == _state(second_map)


def test_empty_text_detects_nothing(detector) -> None:
    detection = detector.detect("", "en")

    assert detection.spans == ()
    assert detection.text == ""


class _CorefPort(NullSemanticDetectionPort):
    """Enabled semantic port whose coreference pass records calls and extends the map."""

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled
        self.coref_calls = 0

    def is_enabled(self) -> bool:
        return self._enabled

    def resolve_coreference(self, *, normalized_text, anon_map, language) -> int:
        self.coref_calls += 1
        anon_map.add_entity("Please", "PERSON_NAME")
        return 1


_COREF_TEXT = "Please write to jane.doe@example.com today."


def _coref_detector(port, *, use_semantic_detection: bool = True) -> Detector:
    config = SeptumCoreConfig(
        use_presidio_layer=True,
        use_ner_layer=False,
        use_semantic_detection=use_semantic_detection,
    )
    return SeptumEngine(regulations=["gdpr"], config=config, semantic_port=port)._detector


def test_semantic_coreference_split_equals_sanitize_and_runs_only_in_apply() -> None:
    whole_port, split_port = _CorefPort(), _CorefPort()
    whole_det, split_det = _coref_detector(whole_port), _coref_detector(split_port)
    whole_map, split_map = _map("en"), _map("en")

    whole = whole_det.sanitize(_COREF_TEXT, "en", whole_map)
    detection = split_det.detect(_COREF_TEXT, "en")
    assert detection.semantic_enabled is True
    assert split_port.coref_calls == 0
    split = split_det.apply(detection, split_map)

    assert whole_port.coref_calls == split_port.coref_calls == 1
    assert "Please" in split_map.entity_map
    assert "Please" not in split.sanitized_text
    assert split == whole
    assert _state(split_map) == _state(whole_map)


def test_coreference_skipped_when_port_disabled_at_detect_time() -> None:
    port = _CorefPort(enabled=False)
    det = _coref_detector(port)
    anon_map = _map("en")

    detection = det.detect(_COREF_TEXT, "en")
    det.apply(detection, anon_map)

    assert detection.semantic_enabled is False
    assert port.coref_calls == 0


def test_coreference_skipped_when_config_flag_off() -> None:
    port = _CorefPort()
    det = _coref_detector(port, use_semantic_detection=False)

    det.apply(det.detect(_COREF_TEXT, "en"), _map("en"))

    assert port.coref_calls == 0


def test_coreference_decision_follows_the_detection_not_the_port_state() -> None:
    port = _CorefPort(enabled=False)
    det = _coref_detector(port)
    detection = det.detect(_COREF_TEXT, "en")
    port._enabled = True

    det.apply(detection, _map("en"))

    assert port.coref_calls == 0


def test_coreference_skipped_when_nothing_was_masked(detector) -> None:
    port = _CorefPort()
    det = _coref_detector(port)

    det.apply(det.detect("Nothing sensitive here.", "en"), _map("en"))

    assert port.coref_calls == 0


def test_offsets_refer_to_nfc_normalized_text(detector) -> None:
    text = "Café owner école: write to jane.doe@example.com now."
    detection = detector.detect(text, "en")

    assert detection.text != text
    assert len(detection.text) < len(text)
    emails = [s for s in detection.spans if s.entity_type == "EMAIL_ADDRESS"]
    assert emails
    assert detection.text[emails[0].start : emails[0].end] == "jane.doe@example.com"

    whole_map, split_map = _map("en"), _map("en")
    whole = detector.sanitize(text, "en", whole_map)
    split = detector.apply(detection, split_map)
    assert split == whole
    assert "jane.doe@example.com" not in split.sanitized_text
    assert split.sanitized_text.startswith("Café owner école: write to ")
    assert _state(split_map) == _state(whole_map)


def test_apply_skips_overlapping_and_blank_spans_but_counts_every_span(detector) -> None:
    from septum_core.spans import DetectedSpan, DetectionResult

    text = "ab jane@example.com   zz"
    email_start = text.index("jane")
    email_end = email_start + len("jane@example.com")
    blank_start = email_end
    blank_end = blank_start + 3
    detection = DetectionResult(
        text=text,
        language="en",
        spans=(
            DetectedSpan(blank_start, blank_end, "EMAIL_ADDRESS", 0.9),
            DetectedSpan(email_start + 2, email_end, "PHONE_NUMBER", 0.9),
            DetectedSpan(email_start, email_end, "EMAIL_ADDRESS", 0.9),
        ),
        semantic_enabled=False,
    )
    anon_map = _map("en")

    result = detector.apply(detection, anon_map)

    assert result.entity_count == 1
    assert len(result.detected_spans) == 1
    assert result.detected_spans[0].start == email_start
    assert "jane@example.com" not in result.sanitized_text
    assert result.sanitized_text.startswith("ab ")
    assert result.sanitized_text.endswith("zz")
    assert list(anon_map.entity_map) == ["jane@example.com"]
    assert result.entity_type_counts == {"EMAIL_ADDRESS": 2, "PHONE_NUMBER": 1}


def test_apply_with_no_spans_returns_text_unchanged(detector) -> None:
    from septum_core.spans import DetectionResult

    result = detector.apply(DetectionResult(text="plain text", language="en", semantic_enabled=False), _map("en"))

    assert result.sanitized_text == "plain text"
    assert result.entity_count == 0
    assert result.entity_type_counts == {}


@pytest.mark.parametrize(
    ("start", "end"), [(-1, 3), (0, 11), (11, 12), (4, 4), (5, 2)]
)
def test_apply_rejects_invalid_span_and_masks_nothing(detector, start, end) -> None:
    from septum_core.spans import DetectedSpan, DetectionResult

    anon_map = _map("en")
    detection = DetectionResult(
        text="0123456789",
        language="en",
        spans=(
            DetectedSpan(0, 2, "EMAIL_ADDRESS", 0.9),
            DetectedSpan(start, end, "EMAIL_ADDRESS", 0.9),
        ),
        semantic_enabled=False,
    )

    with pytest.raises(ValueError):
        detector.apply(detection, anon_map)

    assert anon_map.entity_map == {}


def test_detection_result_repr_hides_text_and_is_frozen() -> None:
    from dataclasses import FrozenInstanceError

    from septum_core.spans import DetectionResult

    detection = DetectionResult(text="secret@example.com", language="en", semantic_enabled=False)

    assert "secret" not in repr(detection)
    with pytest.raises(FrozenInstanceError):
        detection.text = "other"
    with pytest.raises(TypeError):
        DetectionResult("x", "en", (), False)
