"""Tests for the AnonymizationMap and its language-aware behavior."""

from __future__ import annotations

from backend.app.services.anonymization_map import AnonymizationMap


def test_coreference_full_name_and_first_name_share_placeholder() -> None:
    """Coreference: full name and first name should map to the same placeholder."""
    amap = AnonymizationMap(document_id=1, language="tr")

    full_placeholder = amap.add_entity("Ahmet Yılmaz", "PERSON_NAME")
    first_placeholder = amap.add_entity("Ahmet", "PERSON_NAME")

    assert full_placeholder == first_placeholder
    # Both surface forms should be tracked in the map.
    assert "Ahmet Yılmaz" in amap.entity_map
    assert "Ahmet" in amap.entity_map


def test_locale_lower_for_turkish_istanbul() -> None:
    """Locale-aware lowercasing must work correctly for Turkish dotted I.

    CITY is not a person-identifying entity type, so its tokens are
    intentionally NOT propagated to the blocklist (avoids replacing
    every occurrence of common city names throughout a document).
    """
    amap = AnonymizationMap(document_id=2, language="tr")

    ph = amap.add_entity("İstanbul", "CITY")
    assert ph == "[CITY_1]"

    assert "istanbul" not in amap.blocklist
    assert amap.token_to_placeholder.get("istanbul") is None


def test_person_name_tokens_propagated_to_blocklist() -> None:
    """PERSON_NAME tokens must be added to the blocklist for coreference."""
    amap = AnonymizationMap(document_id=6, language="tr")

    ph = amap.add_entity("Mehmet", "PERSON_NAME")
    assert ph == "[PERSON_NAME_1]"

    assert "mehmet" in amap.blocklist
    assert amap.token_to_placeholder.get("mehmet") == "[PERSON_NAME_1]"

    redacted = amap.apply_blocklist("Daha sonra Mehmet geldi.", language="tr")
    assert "[PERSON_NAME_1]" in redacted
    assert "Mehmet" not in redacted


def test_multilingual_german_umlaut_preserved_in_blocklist() -> None:
    """German umlaut characters should be preserved after normalization."""
    amap = AnonymizationMap(document_id=3, language="de")

    ph = amap.add_entity("Müller", "PERSON_NAME")
    assert ph == "[PERSON_NAME_1]"

    assert "müller" in amap.blocklist

    redacted = amap.apply_blocklist("Herr Müller ist hier.", language="de")
    assert "[PERSON_NAME_1]" in redacted
    assert "Müller" not in redacted


def test_short_tokens_not_added_to_blocklist() -> None:
    """Tokens with length <= 2 must not be added to the blocklist."""
    amap = AnonymizationMap(document_id=4, language="en")

    amap.add_entity("AB", "ALIAS")
    assert "ab" not in amap.blocklist

    amap.add_entity("LongToken", "ALIAS")
    assert "longtoken" in amap.blocklist


def test_apply_blocklist_does_not_touch_placeholders() -> None:
    """Existing placeholders must remain intact; token mentions get real placeholder."""
    amap = AnonymizationMap(document_id=5, language="en")
    amap.add_entity("John", "PERSON_NAME")

    text = "User [PERSON_NAME_1] is actually John."
    redacted = amap.apply_blocklist(text, language="en")

    assert "[PERSON_NAME_1]" in redacted
    assert "John" not in redacted
    # Redaction uses [ENTITY_TYPE_N] only, not [BLOCKED].
    assert "[BLOCKED]" not in redacted


# --- Coreference edge cases ---


def test_coreference_initials_via_shared_token() -> None:
    """Initials sharing a token with full name should resolve via that token."""
    amap = AnonymizationMap(document_id=10, language="en")

    ph_full = amap.add_entity("Yilmaz", "PERSON_NAME")
    ph_initial = amap.add_entity("Yilmaz", "LAST_NAME")

    assert ph_full == ph_initial


def test_coreference_mixed_case_same_placeholder() -> None:
    """JOHN DOE and John Doe should map to the same placeholder."""
    amap = AnonymizationMap(document_id=11, language="en")

    ph1 = amap.add_entity("John Doe", "PERSON_NAME")
    ph2 = amap.add_entity("JOHN DOE", "PERSON_NAME")

    assert ph1 == ph2


def test_coreference_hyphenated_name() -> None:
    """Hyphenated name subset should resolve coreference."""
    amap = AnonymizationMap(document_id=12, language="fr")

    ph_full = amap.add_entity("Jean-Pierre Dupont", "PERSON_NAME")
    # "Dupont" is a subset token of the full name
    ph_partial = amap.add_entity("Dupont", "PERSON_NAME")

    assert ph_full == ph_partial


def test_stopword_not_in_blocklist() -> None:
    """Stopwords must never be added to blocklist, even in person names."""
    amap = AnonymizationMap(document_id=13, language="en")
    amap.add_entity("The", "PERSON_NAME")

    # "the" is a stopword and should not be in blocklist
    assert "the" not in amap.blocklist


def test_same_normalized_form_different_types_first_wins() -> None:
    """First entity type seen for a normalized form should win."""
    amap = AnonymizationMap(document_id=14, language="en")

    ph1 = amap.add_entity("Alexander", "PERSON_NAME")
    ph2 = amap.add_entity("Alexander", "FIRST_NAME")

    # Should resolve to the first-seen placeholder (PERSON_NAME)
    assert ph1 == ph2
    assert "PERSON_NAME" in ph1


def test_punctuation_adjacent_token_resolved_by_blocklist() -> None:
    """Tokens adjacent to punctuation should still be caught by blocklist."""
    amap = AnonymizationMap(document_id=15, language="en")
    amap.add_entity("Smith", "PERSON_NAME")

    # The regex \w+ in apply_blocklist should isolate "Smith" from surrounding punctuation
    text = "Contact (Smith) for details."
    redacted = amap.apply_blocklist(text, language="en")
    assert "Smith" not in redacted
    assert "[PERSON_NAME_1]" in redacted

