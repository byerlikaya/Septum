"""The detector sees a name past the first window and reports it once, at its offsets."""

from __future__ import annotations

from ner_fakes import NAME, FakePipeline, StubRegistry, document
from septum_core.anonymization_map import AnonymizationMap
from septum_core.config import SeptumCoreConfig
from septum_core.detector import Detector
from septum_core.ner_model_registry import _WindowedNERPipeline


def test_long_text_gives_one_person_span_at_original_offsets() -> None:
    body, _ = document(700, name_at=[600])
    # An ALL-CAPS word makes the detector run its title-cased second pass too.
    text = body.replace("w3", "NOTICE", 1)
    name = " ".join(NAME)
    at = text.index(name)
    fake = FakePipeline(max_positions=514, model_max_length=512)
    detector = Detector(
        SeptumCoreConfig(use_presidio_layer=False, use_ner_layer=True),
        ner_registry=StubRegistry(_WindowedNERPipeline(fake, None)),
    )

    result = detector.sanitize(text, "en", AnonymizationMap(document_id=0, language="en"))

    assert [(s.start, s.end, s.entity_type) for s in result.detected_spans] == [
        (at, at + len(name), "PERSON_NAME")
    ]
    assert len(fake.chunks) > 1
    assert fake.truncated == 0
