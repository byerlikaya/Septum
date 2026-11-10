from __future__ import annotations

from typing import List

from presidio_analyzer import EntityRecognizer

from septum_api.models.regulation import CustomRecognizer, RegulationRuleset
from septum_api.models.settings import AppSettings
from septum_api.services.anonymization_map import AnonymizationMap
from septum_api.services.policy_composer import ComposedPolicy, PolicyComposer
from septum_api.services.recognizers import registry as registry_module
from septum_api.services.recognizers.registry import (
    LLMContextRecognizer,
    RecognizerRegistry,
)
from septum_api.services.sanitizer import PIISanitizer


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



def test_gdpr_hipaa_union_includes_date_and_gdpr_alone_does_not() -> None:
    """DATE comes from the HIPAA pack only; GDPR alone must not carry it."""
    from septum_core.recognizers.gdpr import ENTITY_TYPES as gdpr_types
    from septum_core.recognizers.hipaa import ENTITY_TYPES as hipaa_types

    gdpr = _make_regulation("gdpr", entity_types=list(gdpr_types))
    hipaa = _make_regulation("hipaa", entity_types=list(hipaa_types))
    composer = PolicyComposer()

    gdpr_only = composer.compose_from_data([gdpr], [], [])
    union = composer.compose_from_data([gdpr, hipaa], [], [])

    assert "DATE" not in gdpr_only.entity_types
    assert "DATE" in union.entity_types
    assert "DATE_OF_BIRTH" in union.entity_types


def _make_custom_llm_recognizer(entity_type: str, llm_prompt: str) -> CustomRecognizer:
    return CustomRecognizer(
        id=2,
        name="Project Codename",
        entity_type=entity_type,
        detection_method="llm_prompt",
        pattern=None,
        keywords=None,
        llm_prompt=llm_prompt,
        context_words=[],
        placeholder_label=entity_type,
        is_active=True,
    )


def test_llm_prompt_custom_rule_gets_a_recognizer() -> None:
    """An active llm_prompt rule is composed into the Ollama-backed recognizer, not dropped."""
    custom = _make_custom_llm_recognizer("PROJECT_CODENAME", "Find internal project codenames.")

    policy = PolicyComposer().compose_from_data([], [custom], [])

    assert "PROJECT_CODENAME" in policy.entity_types
    assert [type(r) for r in policy.recognizers] == [LLMContextRecognizer]
    assert policy.recognizers[0].supported_entities == ["PROJECT_CODENAME"]


def test_sanitizer_masks_what_the_llm_prompt_rule_detects(monkeypatch) -> None:
    """The composed llm_prompt recognizer runs inside the sanitizer; the Ollama call is mocked."""
    text = "The launch of Bluefinch moves to next quarter."
    prompts: list[str] = []

    def _fake_ollama(prompt: str, **_kwargs) -> str:
        prompts.append(prompt)
        return '[{"text": "Bluefinch"}]'

    monkeypatch.setattr(registry_module, "request_ollama_sync", _fake_ollama)
    custom = _make_custom_llm_recognizer("PROJECT_CODENAME", "Find internal project codenames.")
    policy = PolicyComposer().compose_from_data([], [custom], [])
    settings = AppSettings(
        id=1,
        use_presidio_layer=True,
        use_ner_layer=False,
        use_ollama_validation_layer=False,
        use_ollama_layer=False,
    )
    anon_map = AnonymizationMap(document_id=1, language="en")

    result = PIISanitizer(settings=settings, policy=policy).sanitize(
        text=text, language="en", anon_map=anon_map
    )

    assert prompts
    assert "Bluefinch" not in result.sanitized_text
    assert "Bluefinch" in anon_map.entity_map


def test_injected_recognizer_registry_is_honoured() -> None:
    """A registry passed to the composer is used instead of the backend default."""
    sentinel = EntityRecognizer(supported_entities=["PROJECT_CODENAME"], name="injected")

    class _FixedRegistry(RecognizerRegistry):
        def build(self, active_regs, active_custom):
            return [sentinel]

    custom = _make_custom_llm_recognizer("PROJECT_CODENAME", "Find internal project codenames.")

    policy = PolicyComposer(_FixedRegistry()).compose_from_data([], [custom], [])

    assert policy.recognizers == [sentinel]


async def test_compose_with_snapshot_yields_llm_recognizer_only_for_active_rules(env) -> None:
    """The DB-backed entry point used by chat and ingestion keeps active llm_prompt rules, drops inactive ones."""
    active = _make_custom_llm_recognizer("PROJECT_CODENAME", "Find internal project codenames.")
    inactive = _make_custom_llm_recognizer("SECRET_CLIENT", "Find secret client names.")
    inactive.id = 3
    inactive.name = "Secret Client"
    inactive.is_active = False
    async with env.session_maker() as session:
        session.add_all([active, inactive])
        await session.commit()
        settings = AppSettings(id=1)

        policy, snapshot = await PolicyComposer().compose_with_snapshot(session, settings)

    llm_recognizers = [r for r in policy.recognizers if isinstance(r, LLMContextRecognizer)]
    assert [r.supported_entities for r in llm_recognizers] == [["PROJECT_CODENAME"]]
    assert "SECRET_CLIENT" not in policy.entity_types
    assert snapshot is not None
