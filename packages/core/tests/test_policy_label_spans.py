"""A span that is only a name the policy defines (an entity type, a regulation id) is no PII.

NER models tag field labels and law acronyms ("IBAN:" above an IBAN, "KVKK" in
a heading) as persons or organizations. Registered in the map, such a label
would be masked in every later text, the user's question included.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import pytest
from ner_fakes import FakePipeline, StubRegistry
from septum_core import SeptumEngine
from septum_core.config import SeptumCoreConfig
from septum_core.ner_model_registry import _WindowedNERPipeline
from septum_core.ports import NullSemanticDetectionPort
from septum_core.regulations.composer import PolicyComposer
from septum_core.spans import DetectedSpan

_NER_ONLY = SeptumCoreConfig(use_presidio_layer=False, use_ner_layer=True)
_PRESIDIO_ONLY = SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=False)


def _engine(emit: dict[str, str]) -> SeptumEngine:
    registry = StubRegistry(_WindowedNERPipeline(FakePipeline(emit=emit), None))
    return SeptumEngine(regulations=["kvkk", "popia"], config=_NER_ONLY, ner_registry=registry)


@dataclass
class _Regulation:
    id: str
    entity_types: List[str]


@dataclass
class _Custom:
    id: int
    name: str
    entity_type: str
    detection_method: str
    pattern: Optional[str] = None
    keywords: Optional[List[str]] = None
    llm_prompt: Optional[str] = None
    context_words: List[str] = field(default_factory=list)
    placeholder_label: str = ""
    is_active: bool = True


_SEMANTIC_TEXT = "Maaş her ayın son iş günü işçinin bildirdiği IBAN hesabına yatırılır ve dekontu saklanır."


class _AliasPort(NullSemanticDetectionPort):
    """An enabled semantic port whose alias layer tags the word "IBAN" as a person."""

    def is_enabled(self) -> bool:
        return True

    def detect_aliases(self, *, normalized_text: str) -> List[DetectedSpan]:
        start = normalized_text.index("IBAN")
        return [DetectedSpan(start=start, end=start + 4, entity_type="PERSON_NAME", score=0.99)]


def test_entity_type_label_tagged_as_person_is_not_masked() -> None:
    engine = _engine({"IBAN": "PER", "Ada Lovelace": "PER"})

    result = engine.mask("IBAN:\nTR00 0000\nAda Lovelace", language="tr")

    assert result.masked_text.startswith("IBAN:")
    assert [s.entity_type for s in result.detected_spans] == ["PERSON_NAME"]


def test_label_with_a_suffix_after_an_apostrophe_is_not_masked() -> None:
    engine = _engine({"IBAN’a": "PER"})

    result = engine.mask("Maaş hangi IBAN’a yatırılıyor?", language="tr")

    assert "IBAN’a" in result.masked_text
    assert result.detected_spans == []


def test_regulation_id_tagged_as_organization_is_not_masked() -> None:
    engine = _engine({"KVKK": "ORG"})

    result = engine.mask("⚠ KVKK AYDINLATMA METNİ", language="tr")

    assert "KVKK" in result.masked_text
    assert result.detected_spans == []


def test_span_that_only_contains_a_label_is_still_masked() -> None:
    engine = _engine({"Iban Holdings": "ORG"})

    result = engine.mask("Iban Holdings signed.", language="en")

    assert "Iban Holdings" not in result.masked_text


@pytest.mark.parametrize(
    ("emit", "text", "language"),
    [
        ({"IBAN's": "PER"}, "Send it to the IBAN's owner.", "en"),
        ({"iban": "PER"}, "which iban is it", "en"),
        ({"Email Address": "PER"}, "Email Address: none", "en"),
        ({"Credit Card": "ORG"}, "Credit Card: none", "en"),
    ],
    ids=["straight-apostrophe", "lowercase", "underscore-type", "alias-type"],
)
def test_label_variants_from_ner_are_not_masked(
    emit: dict[str, str], text: str, language: str
) -> None:
    result = _engine(emit).mask(text, language=language)

    assert result.masked_text == text
    assert result.detected_spans == []


def test_custom_keyword_type_named_like_its_match_still_masks() -> None:
    policy = PolicyComposer().compose_from_data(
        [_Regulation(id="gdpr", entity_types=["PERSON_NAME"])],
        [_Custom(id=1, name="titan", entity_type="PROJECT_TITAN",
                 detection_method="keyword_list", keywords=["Project Titan"])],
        [],
    )
    engine = SeptumEngine(config=_PRESIDIO_ONLY, policy=policy)

    result = engine.mask("Project Titan launched.", language="en")

    assert "Project Titan" not in result.masked_text
    assert [s.entity_type for s in result.detected_spans] == ["PROJECT_TITAN"]


def test_custom_type_names_are_not_label_terms() -> None:
    policy = PolicyComposer().compose_from_data(
        [_Regulation(id="gdpr", entity_types=["PERSON_NAME"])],
        [_Custom(id=1, name="titan", entity_type="PROJECT_TITAN",
                 detection_method="keyword_list", keywords=["Nothing"])],
        [],
    )
    registry = StubRegistry(
        _WindowedNERPipeline(FakePipeline(emit={"Project Titan": "PER"}), None)
    )
    engine = SeptumEngine(config=_NER_ONLY, policy=policy, ner_registry=registry)

    result = engine.mask("Project Titan launched.", language="en")

    assert "Project Titan" not in result.masked_text


def test_label_from_the_semantic_layer_is_not_masked() -> None:
    engine = SeptumEngine(
        regulations=["kvkk"],
        config=SeptumCoreConfig(
            use_presidio_layer=False, use_ner_layer=False, use_semantic_detection=True
        ),
        semantic_port=_AliasPort(),
    )

    result = engine.mask(_SEMANTIC_TEXT, language="tr")

    assert result.masked_text == _SEMANTIC_TEXT
    assert result.detected_spans == []
