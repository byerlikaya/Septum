"""Chat-time unification of the query and document placeholder spaces.

Each document's placeholder moves to exactly one global placeholder, so a
chunk keeps naming the person its own document meant, and the unified map
the PII gate masks against never points a known name at the wrong person.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from sanitizer_doubles import PatternSanitizer, literal
from septum_api.routers.chat import (
    _apply_chunk_placeholder_remap,
    _build_unified_placeholder_space,
)
from septum_api.services import pii_gate
from septum_api.services.anonymization_map import AnonymizationMap
from septum_core.unmasker import Unmasker

P1, P2, P3, P4 = (f"[PERSON_NAME_{n}]" for n in range(1, 5))


def _doc(doc_id: int, language: str, *names: str) -> AnonymizationMap:
    anon_map = AnonymizationMap(document_id=doc_id, language=language)
    for name in names:
        anon_map.add_entity(name, "PERSON_NAME")
    return anon_map


def _two_alis() -> dict[int, AnonymizationMap]:
    return {1: _doc(1, "tr", "Ali Yılmaz", "Ali"), 2: _doc(2, "tr", "Ali Kaya", "Ali")}


def _restore(text: str, remap: dict[str, str], unified: AnonymizationMap) -> str:
    return Unmasker().unmask(_apply_chunk_placeholder_remap(text, remap), unified)


def test_one_document_placeholder_moves_to_exactly_one_global() -> None:
    maps = {1: _doc(1, "tr", "Ahmet Kaya", "Ahmet Kaya’nın", "AHMET KAYA"), 2: _doc(2, "tr", "Ahmet Kaya")}

    remaps, unified = _build_unified_placeholder_space(maps)

    assert remaps == {1: {}, 2: {}}
    assert unified.placeholder_lookup == {P1: "Ahmet Kaya"}


def test_same_bare_name_in_two_documents_keeps_each_document_s_person() -> None:
    remaps, unified = _build_unified_placeholder_space(_two_alis())

    assert _restore(f"{P1} signed", remaps[1], unified) == "Ali Yılmaz signed"
    assert _restore(f"{P1} paid", remaps[2], unified) == "Ali Kaya paid"
    assert unified.placeholder_lookup == {P1: "Ali Yılmaz", P2: "Ali Kaya", P3: "Ali"}


def test_shared_surname_never_points_at_one_person_in_the_unified_map() -> None:
    maps = {
        1: _doc(1, "en", "John Smith", "Jane Smith", "Smith"),
        2: _doc(2, "en", "Smith", "John Smith"),
    }

    remaps, unified = _build_unified_placeholder_space(maps)

    assert remaps == {1: {}, 2: {}}
    assert unified.token_to_placeholder["smith"] == P3
    assert unified.entity_map["Smith"] == P3
    assert unified.placeholder_lookup[P3] == "Smith"
    assert _restore(f"{P1} called", remaps[2], unified) == "John Smith called"


def test_gate_on_the_unified_map_masks_known_and_new_names_apart() -> None:
    _, unified = _build_unified_placeholder_space(_two_alis())
    sanitizer = PatternSanitizer(literal("Ayşe Demir", "PERSON_NAME"))

    out = pii_gate._gate_one("Ali Kaya met Ayşe Demir. Ali agreed.", unified, sanitizer, "tr")

    assert out == f"{P2} met {P4}. {P3} agreed."
    assert unified.placeholder_lookup[P4] == "Ayşe Demir"
    assert unified.add_entity("Ali", "PERSON_NAME") == P3
    assert Unmasker().unmask(out, unified) == "Ali Kaya met Ayşe Demir. Ali agreed."


def test_pronoun_one_document_holds_never_names_its_person_in_another_s_chunk() -> None:
    jane = _doc(1, "en", "Jane Roe")
    jane.token_to_placeholder["she"] = P1
    _, unified = _build_unified_placeholder_space({-1: AnonymizationMap(document_id=0), 1: jane, 2: _doc(2, "en", "John Poe")})

    retried = pii_gate._gate_one("Then she paid.", unified, PatternSanitizer(), "en")

    assert P1 not in retried
    assert Unmasker().unmask(retried, unified) == "Then she paid."


def test_custom_entity_types_stay_masked_in_the_unified_map() -> None:
    a = AnonymizationMap(document_id=1, language="en")
    mrn = a.add_entity("PX-4471-ZQ", "MRN2")
    a.add_entity("Ali Kaya", "PERSON_NAME")
    b = AnonymizationMap(document_id=2, language="en")
    code = b.add_entity("Codename Bluebird", "Project code")
    _, unified = _build_unified_placeholder_space({-1: AnonymizationMap(document_id=0), 1: a, 2: b})

    masked = pii_gate.mask_known_entities("PX-4471-ZQ belongs to Codename Bluebird and Ali Kaya.", unified)

    assert masked == f"{mrn} belongs to {code} and {P1}."
    assert Unmasker().unmask(masked, unified) == "PX-4471-ZQ belongs to Codename Bluebird and Ali Kaya."


def _log_inputs() -> tuple[AnonymizationMap, list[str], list[str]]:
    anon_map = AnonymizationMap(document_id=1, language="tr")
    person = anon_map.add_entity("Ayşe Demir", "PERSON_NAME")
    national_id = anon_map.add_entity("12345678901", "NATIONAL_ID")
    bare = anon_map.add_entity("Ali", "PERSON_NAME")
    answers = [f"Ref [{person}_2] noted", f"id [{national_id}_1]", f"see [{bare}_7]", f"[Note: {person} paid_3]"]
    return anon_map, answers, ["Ayşe Demir", "12345678901", "Ali"]


def test_surviving_placeholder_log_never_holds_an_original(caplog) -> None:
    from septum_api.routers import chat as chat_module

    anon_map, answers, originals = _log_inputs()
    settings = SimpleNamespace(deanon_strategy="simple")
    caplog.set_level(logging.DEBUG, logger=chat_module.logger.name)

    for answer in answers:
        restored = Unmasker().unmask(answer, anon_map)
        chat_module._log_surviving_placeholders("s1", settings, anon_map, answer, restored)

    logged = " ".join(record.getMessage() for record in caplog.records)
    assert logged
    assert not [original for original in originals if original in logged]


def test_unrestored_placeholder_is_still_logged(caplog) -> None:
    from septum_api.routers import chat as chat_module

    anon_map, _, _ = _log_inputs()
    caplog.set_level(logging.DEBUG, logger=chat_module.logger.name)
    answer = "[PERSON_NAME_9] left"

    chat_module._log_surviving_placeholders(
        "s1", SimpleNamespace(deanon_strategy="simple"), anon_map, answer, Unmasker().unmask(answer, anon_map)
    )

    assert any("residual=['[PERSON_NAME_9]']" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    "message", ["Summarize [Clause 4_2] please", "What is [Madde 5_1]?", "Explain [ref_12]"]
)
def test_bracketed_text_the_maps_do_not_hold_is_no_typed_placeholder(message: str) -> None:
    from septum_api.routers import chat as chat_module

    assert chat_module._typed_query_placeholders(message, message, {}, {"[MRN2_1]"}) == []


def test_typed_custom_placeholder_the_turn_holds_is_kept() -> None:
    from septum_api.routers import chat as chat_module

    message = "Who owns [MRN2_1] and [PERSON_NAME_2]?"
    sanitized = "Who owns [MRN2_3] and [PERSON_NAME_2]?"

    typed = chat_module._typed_query_placeholders(message, sanitized, {"[MRN2_1]": "[MRN2_3]"}, {"[MRN2_3]"})

    assert typed == ["[MRN2_3]", "[PERSON_NAME_2]"]
