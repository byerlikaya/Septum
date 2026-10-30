"""Shared name parts at the API layer: anon summary counts and the PII gate.

A name part shared by two people gets its own neutral placeholder inside
``add_entity``. The anon summary must not count the extra alias entries as
extra people, and the PII gate's mirrored ``placeholder_lookup`` must resolve
the neutral placeholder so it never reaches a chat answer raw.
"""

import re

import pytest
from sanitizer_doubles import PatternSanitizer
from septum_api.routers import documents as documents_module
from septum_api.services import document_anon_store, pii_gate
from septum_api.services.anonymization_map import AnonymizationMap
from septum_core.unmasker import Unmasker


async def test_anon_summary_counts_distinct_placeholders(monkeypatch: pytest.MonkeyPatch) -> None:
    anon_map = AnonymizationMap(document_id=5, language="en")
    anon_map.add_entity("John Smith", "PERSON_NAME")
    anon_map.add_entity("Smith", "PERSON_NAME")
    anon_map.add_entity("John", "PERSON_NAME")
    anon_map.add_entity("Jane Smith", "PERSON_NAME")
    anon_map.add_entity("jane@example.com", "EMAIL_ADDRESS")

    async def owned(*_args, **_kwargs):
        return None

    async def stored(_document_id: int) -> AnonymizationMap:
        return anon_map

    monkeypatch.setattr(documents_module, "get_owned_or_404", owned)
    monkeypatch.setattr(document_anon_store, "get_document_map", stored)

    summary = await documents_module.get_anon_summary(5, db=None, user=None)

    distinct = len(set(anon_map.entity_map.values())) - 1
    assert len(anon_map.entity_map) - 1 > distinct
    assert summary == {
        "document_id": 5,
        "entities": {"PERSON_NAME": distinct, "EMAIL_ADDRESS": 1},
        "total": distinct + 1,
    }


async def test_gate_mirror_lookup_resolves_a_neutral_placeholder() -> None:
    anon_map = AnonymizationMap(document_id=0, language="en")
    email = anon_map.add_entity("jane@example.com", "EMAIL_ADDRESS")
    anon_map.placeholder_lookup[email] = "jane@example.com"
    sanitizer = PatternSanitizer((re.compile(r"Ali (?:Kaya|Demir)"), "PERSON_NAME"))
    texts = ["Ali Kaya met Ali Demir.", "Ali left, mail jane@example.com."]

    gated = await pii_gate.gate_untrusted_texts(
        texts, anon_map, None, None, language="en", sanitizer=sanitizer
    )
    kaya, demir = anon_map.entity_map["Ali Kaya"], anon_map.entity_map["Ali Demir"]
    shared = anon_map.entity_map["Ali"]

    assert gated == [f"{kaya} met {demir}.", f"{shared} left, mail {email}."]
    assert shared not in (kaya, demir)
    assert anon_map.placeholder_lookup[shared] == "Ali"
    restored = Unmasker().unmask(" ".join(gated), anon_map)
    assert restored == " ".join(texts)
    assert not re.search(r"\[[A-Z_]+_\d+\]", restored)
