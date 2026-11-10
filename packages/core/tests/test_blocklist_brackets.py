"""Blocklist redaction inside bracketed text.

Only the placeholder shape a map mints is kept whole by ``apply_blocklist``;
any other bracketed text (Markdown link text, citations, notes) is ordinary
text, so a known name inside it is redacted.
"""

from __future__ import annotations

import pytest
from septum_core.anonymization_map import PLACEHOLDER_SHAPE, AnonymizationMap


def _map() -> AnonymizationMap:
    anon_map = AnonymizationMap(document_id=1, language="en")
    anon_map.add_entity("Sarah Connor", "PERSON_NAME")
    return anon_map


@pytest.mark.parametrize(
    "text",
    [
        "See [notes from sarah](http://x/y) and [Ref: sarah called].",
        "[sarah]",
        "[[sarah]]",
        "[note: [sarah] said",
        "unclosed [sarah wrote",
        "[first line\nsarah second line]",
        "[sarah notes, about sarah]",
        "[x] sarah [y]",
    ],
)
def test_known_name_in_bracketed_text_is_redacted(text: str) -> None:
    anon_map = _map()
    placeholder = anon_map.token_to_placeholder["sarah"]

    redacted = anon_map.apply_blocklist(text)

    assert "sarah" not in redacted.lower()
    assert placeholder in redacted


def test_link_text_and_citation_keep_their_structure() -> None:
    anon_map = _map()
    placeholder = anon_map.token_to_placeholder["sarah"]

    redacted = anon_map.apply_blocklist("See [notes from sarah](http://x/y) and [Ref: sarah called].")

    assert redacted == (
        f"See [notes from {placeholder}](http://x/y) and [Ref: {placeholder} called]."
    )


def _minted_placeholders() -> tuple[AnonymizationMap, list[str]]:
    """A map holding every placeholder form the map mints."""
    anon_map = AnonymizationMap(document_id=1, language="en")
    anon_map.add_entity("John Smith", "PERSON_NAME")
    anon_map.add_entity("Jane Smith", "PERSON_NAME")
    anon_map.add_entity("Smith", "PERSON_NAME")
    anon_map.add_entity("sarah@example.com", "EMAIL_ADDRESS")
    anon_map.add_entity("ACME-77", "Project code")
    anon_map.add_entity("X1", "MRN2")
    minted = sorted(
        {
            *anon_map.entity_map.values(),
            *anon_map.token_to_placeholder.values(),
            anon_map.mint("PERSON_NAME"),
            anon_map.inert("[PERSON_1]"),
            anon_map.inert("[Custom note_4]"),
            anon_map.inert(anon_map.entity_map["John Smith"]),
        }
    )
    return anon_map, minted


def test_every_minted_placeholder_is_left_as_written() -> None:
    anon_map, minted = _minted_placeholders()
    assert all(PLACEHOLDER_SHAPE.fullmatch(p) for p in minted)
    assert anon_map.token_to_placeholder["smith"] not in {
        anon_map.entity_map["John Smith"],
        anon_map.entity_map["Jane Smith"],
    }
    text = " ".join(minted) + " " + "".join(minted) + " " + "\n".join(f"({p})" for p in minted)

    assert anon_map.apply_blocklist(text) == text


def test_redaction_is_idempotent() -> None:
    anon_map, _ = _minted_placeholders()
    anon_map.add_entity("Sarah Connor", "PERSON_NAME")
    text = (
        "John Smith met [jane](http://x) and [Ref: smith, sarah].\n"
        "Unclosed [connor and [nested [sarah]] note"
    )

    once = anon_map.apply_blocklist(text)
    twice = anon_map.apply_blocklist(once)

    assert twice == once
    for name in ("john", "smith", "jane", "sarah", "connor"):
        assert name not in once.lower()
