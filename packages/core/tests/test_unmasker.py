from __future__ import annotations

"""Unit tests for :class:`septum_core.unmasker.Unmasker`.

These exercise the de-anonymization contract in isolation, without
any LLM strategy attached (that lives in the backend shim). Each
test constructs an :class:`AnonymizationMap` directly, seeds a few
entities, and asserts that the placeholders present in a simulated
LLM response are swapped back to their original values.
"""

from septum_core.anonymization_map import AnonymizationMap
from septum_core.unmasker import Unmasker


def _make_map(entries: dict[str, str]) -> AnonymizationMap:
    amap = AnonymizationMap(document_id=0, language="en")
    for original, placeholder in entries.items():
        amap.entity_map[original] = placeholder
    return amap


def test_unmask_returns_input_when_entity_map_is_empty() -> None:
    unmasker = Unmasker()
    amap = AnonymizationMap(document_id=0, language="en")
    assert unmasker.unmask("Hello world", amap) == "Hello world"


def test_unmask_replaces_full_placeholder_form() -> None:
    unmasker = Unmasker()
    amap = _make_map({"Jane Smith": "[PERSON_NAME_1]"})

    result = unmasker.unmask("[PERSON_NAME_1] signed the document.", amap)

    assert result == "Jane Smith signed the document."


def test_unmask_also_understands_short_person_alias() -> None:
    """LLMs often return ``[PERSON_1]`` instead of ``[PERSON_NAME_1]``."""
    unmasker = Unmasker()
    amap = _make_map({"Jane Smith": "[PERSON_NAME_1]"})

    result = unmasker.unmask("Then [PERSON_1] replied to the email.", amap)

    assert result == "Then Jane Smith replied to the email."


def test_unmask_understands_short_organization_alias() -> None:
    unmasker = Unmasker()
    amap = _make_map({"Acme Corp": "[ORGANIZATION_NAME_1]"})

    result = unmasker.unmask("[ORGANIZATION_1] is the buyer.", amap)

    assert result == "Acme Corp is the buyer."


def test_unmask_handles_multiple_entries_in_single_pass() -> None:
    unmasker = Unmasker()
    amap = _make_map(
        {
            "Jane Smith": "[PERSON_NAME_1]",
            "Acme Corp": "[ORGANIZATION_NAME_1]",
            "jane@example.com": "[EMAIL_ADDRESS_1]",
        }
    )

    source = (
        "Re: [PERSON_NAME_1] from [ORGANIZATION_1] — contact [EMAIL_ADDRESS_1]."
    )
    result = unmasker.unmask(source, amap)

    assert result == "Re: Jane Smith from Acme Corp — contact jane@example.com."


def test_unmask_is_idempotent_when_no_placeholder_matches() -> None:
    unmasker = Unmasker()
    amap = _make_map({"Jane Smith": "[PERSON_NAME_1]"})

    text = "No placeholder here."
    assert unmasker.unmask(text, amap) == text
