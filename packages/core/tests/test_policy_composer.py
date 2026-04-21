from __future__ import annotations

"""Unit tests for :class:`septum_core.regulations.composer.PolicyComposer`.

These exercise the pure, database-free composition path. Regulation
and custom-recognizer rows are represented by lightweight dataclasses
that expose exactly the attribute surface declared by the Protocol
types in :mod:`septum_core.regulations.models` — proving that the
composer only relies on duck-typing and not on the backend SQLAlchemy
models.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from presidio_analyzer import EntityRecognizer

from septum_core.regulations.composer import ComposedPolicy, PolicyComposer


@dataclass
class _FakeRegulation:
    id: str
    entity_types: List[str]


@dataclass
class _FakeCustomRecognizer:
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


@dataclass
class _FakeNonPiiRule:
    pattern_type: str
    pattern: str
    languages: List[str]
    entity_types: List[str]
    min_score: Optional[float]
    is_active: bool = True


def test_compose_from_data_unions_entity_types_across_regulations() -> None:
    composer = PolicyComposer()

    policy = composer.compose_from_data(
        active_regs=[
            _FakeRegulation(id="gdpr", entity_types=["EMAIL_ADDRESS", "PHONE_NUMBER"]),
            _FakeRegulation(id="kvkk", entity_types=["PHONE_NUMBER", "NATIONAL_ID"]),
        ],
        active_custom=[],
        active_non_pii=[],
    )

    assert policy.entity_types == ["EMAIL_ADDRESS", "NATIONAL_ID", "PHONE_NUMBER"]
    assert policy.regulation_ids == ["gdpr", "kvkk"]
    assert isinstance(policy, ComposedPolicy)


def test_compose_from_data_includes_active_custom_entity_types() -> None:
    composer = PolicyComposer()

    policy = composer.compose_from_data(
        active_regs=[_FakeRegulation(id="gdpr", entity_types=["EMAIL_ADDRESS"])],
        active_custom=[
            _FakeCustomRecognizer(
                id=1,
                name="employee-id",
                entity_type="EMPLOYEE_ID",
                detection_method="regex",
                pattern=r"EMP-\d{6}",
            )
        ],
        active_non_pii=[],
    )

    assert "EMPLOYEE_ID" in policy.entity_types
    assert "EMAIL_ADDRESS" in policy.entity_types


def test_compose_from_data_skips_inactive_custom_recognizers() -> None:
    composer = PolicyComposer()

    policy = composer.compose_from_data(
        active_regs=[_FakeRegulation(id="gdpr", entity_types=["EMAIL_ADDRESS"])],
        active_custom=[
            _FakeCustomRecognizer(
                id=1,
                name="inactive",
                entity_type="SHOULD_NOT_APPEAR",
                detection_method="regex",
                pattern=r"X",
                is_active=False,
            )
        ],
        active_non_pii=[],
    )

    assert "SHOULD_NOT_APPEAR" not in policy.entity_types


def test_compose_from_data_loads_built_in_recognizer_packs() -> None:
    composer = PolicyComposer()

    policy = composer.compose_from_data(
        active_regs=[_FakeRegulation(id="kvkk", entity_types=["NATIONAL_ID"])],
        active_custom=[],
        active_non_pii=[],
    )

    assert len(policy.recognizers) > 0
    assert all(isinstance(r, EntityRecognizer) for r in policy.recognizers)


def test_compose_from_data_passes_non_pii_rules_through() -> None:
    composer = PolicyComposer()
    rule = _FakeNonPiiRule(
        pattern_type="token",
        pattern="merhaba",
        languages=["tr"],
        entity_types=["PERSON_NAME"],
        min_score=0.7,
    )

    policy = composer.compose_from_data(
        active_regs=[],
        active_custom=[],
        active_non_pii=[rule],
    )

    assert policy.non_pii_rules == [rule]


def test_compose_from_data_skips_missing_regulation_packs() -> None:
    composer = PolicyComposer()

    policy = composer.compose_from_data(
        active_regs=[_FakeRegulation(id="no_such_regulation", entity_types=["EMAIL_ADDRESS"])],
        active_custom=[],
        active_non_pii=[],
    )

    assert policy.regulation_ids == ["no_such_regulation"]
    assert policy.entity_types == ["EMAIL_ADDRESS"]
    assert policy.recognizers == []
