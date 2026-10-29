"""NER spans are kept when the policy names the type or a child the type covers."""

from __future__ import annotations

from ner_fakes import FakePipeline, StubRegistry
from septum_core import SeptumEngine
from septum_core.config import SeptumCoreConfig
from septum_core.ner_model_registry import _WindowedNERPipeline
from septum_core.recognizers import entity_types_for

_NER_ONLY = SeptumCoreConfig(use_presidio_layer=False, use_ner_layer=True)


def test_city_only_pack_masks_ner_location() -> None:
    assert "CITY" in entity_types_for("hipaa")
    assert "LOCATION" not in entity_types_for("hipaa")
    fake = FakePipeline(emit={"Springfield": "LOC"})
    registry = StubRegistry(_WindowedNERPipeline(fake, None))
    engine = SeptumEngine(regulations=["hipaa"], config=_NER_ONLY, ner_registry=registry)

    result = engine.mask("The patient lives in Springfield.", language="en")

    assert "Springfield" not in result.masked_text
    assert [s.entity_type for s in result.detected_spans] == ["LOCATION"]


def test_type_outside_the_policy_is_still_dropped() -> None:
    fake = FakePipeline(emit={"Acme": "ORG"})
    registry = StubRegistry(_WindowedNERPipeline(fake, None))
    engine = SeptumEngine(regulations=["hipaa"], config=_NER_ONLY, ner_registry=registry)

    result = engine.mask("The patient works at Acme.", language="en")

    assert "Acme" in result.masked_text
    assert result.detected_spans == []
