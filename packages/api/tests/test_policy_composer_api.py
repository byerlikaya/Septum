from __future__ import annotations

from typing import List

from presidio_analyzer import EntityRecognizer

from septum_api.models.regulation import CustomRecognizer, RegulationRuleset
from septum_api.services.policy_composer import ComposedPolicy, PolicyComposer


def _make_regulation(
    reg_id: str,
    entity_types: List[str],
) -> RegulationRuleset:
    return RegulationRuleset(
        id=reg_id,
        display_name=reg_id.upper(),
        region="test",
        description=None,
        official_url=None,
        entity_types=entity_types,
        is_builtin=True,
        is_active=True,
        custom_notes=None,
    )


def _make_custom_regex_recognizer(
    name: str,
    entity_type: str,
    pattern: str,
) -> CustomRecognizer:
    return CustomRecognizer(
        id=1,
        name=name,
        entity_type=entity_type,
        detection_method="regex",
        pattern=pattern,
        keywords=None,
        llm_prompt=None,
        context_words=[],
        placeholder_label=entity_type,
        is_active=True,
    )


def test_gdpr_only_entity_types_match_ruleset() -> None:
    """Active: GDPR only → composed entity types should match the ruleset."""
    gdpr = _make_regulation(
        "gdpr",
        entity_types=[
            "PERSON_NAME",
            "EMAIL_ADDRESS",
            "PHONE_NUMBER",
        ],
    )

    composer = PolicyComposer()
    policy: ComposedPolicy = composer.compose_from_data([gdpr], [], [])

    assert set(policy.entity_types) == set(gdpr.entity_types)
    assert policy.regulation_ids == ["gdpr"]


def test_gdpr_and_hiaa_union_entity_types() -> None:
    """Active: GDPR + HIPAA → entity types are the union of both."""
    gdpr = _make_regulation(
        "gdpr",
        entity_types=[
            "PERSON_NAME",
            "EMAIL_ADDRESS",
        ],
    )
    hipaa = _make_regulation(
        "hipaa",
        entity_types=[
            "PERSON_NAME",
            "MEDICAL_RECORD_NUMBER",
        ],
    )

    composer = PolicyComposer()
    policy: ComposedPolicy = composer.compose_from_data([gdpr, hipaa], [], [])

    assert set(policy.entity_types) == {
        "PERSON_NAME",
        "EMAIL_ADDRESS",
        "MEDICAL_RECORD_NUMBER",
    }
    assert policy.regulation_ids == ["gdpr", "hipaa"]


def test_custom_rule_entity_type_included_in_policy() -> None:
    """Active: custom rule + GDPR → both appear in the composed policy."""
    gdpr = _make_regulation(
        "gdpr",
        entity_types=["EMAIL_ADDRESS"],
    )
    custom = _make_custom_regex_recognizer(
        name="Patient File Number",
        entity_type="PATIENT_FILE_NUMBER",
        pattern=r"PFN-[0-9]{6}",
    )

    composer = PolicyComposer()
    policy: ComposedPolicy = composer.compose_from_data([gdpr], [custom], [])

    assert "EMAIL_ADDRESS" in policy.entity_types
    assert "PATIENT_FILE_NUMBER" in policy.entity_types

    # Ensure that at least one recognizer in the composed policy is wired to
    # the custom entity type.
    assert any(
        isinstance(r, EntityRecognizer)
        and "PATIENT_FILE_NUMBER" in getattr(r, "supported_entities", [])
        for r in policy.recognizers
    )

