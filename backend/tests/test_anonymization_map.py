"""Tests for the AnonymizationMap and its language-aware behavior."""

from __future__ import annotations

from backend.app.services.anonymization_map import (
    AnonymizationMap,
    BLOCKLIST_PLACEHOLDER,
)


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
    """Locale-aware lowercasing must work correctly for Turkish dotted I."""
    amap = AnonymizationMap(document_id=2, language="tr")

    amap.add_entity("İstanbul", "CITY")

    # Blocklist should contain the normalized, locale-lowered token.
    assert "istanbul" in amap.blocklist

    redacted = amap.apply_blocklist("İSTANBUL çok güzel bir şehir.", language="tr")
    assert BLOCKLIST_PLACEHOLDER in redacted
    assert "İSTANBUL" not in redacted


def test_multilingual_german_umlaut_preserved_in_blocklist() -> None:
    """German umlaut characters should be preserved after normalization."""
    amap = AnonymizationMap(document_id=3, language="de")

    amap.add_entity("Müller", "PERSON_NAME")

    # Normalization should preserve the umlaut while lowercasing.
    assert "müller" in amap.blocklist

    redacted = amap.apply_blocklist("Herr Müller ist hier.", language="de")
    assert BLOCKLIST_PLACEHOLDER in redacted
    assert "Müller" not in redacted


def test_short_tokens_not_added_to_blocklist() -> None:
    """Tokens with length <= 2 must not be added to the blocklist."""
    amap = AnonymizationMap(document_id=4, language="en")

    amap.add_entity("AB", "ALIAS")
    assert "ab" not in amap.blocklist

    amap.add_entity("LongToken", "ALIAS")
    assert "longtoken" in amap.blocklist


def test_apply_blocklist_does_not_touch_placeholders() -> None:
    """Existing placeholders must remain intact when applying the blocklist."""
    amap = AnonymizationMap(document_id=5, language="en")
    amap.blocklist.add("john")

    text = "User [PERSON_NAME_1] is actually John."
    redacted = amap.apply_blocklist(text, language="en")

    # Placeholder must remain unchanged, while the raw name is redacted.
    assert "[PERSON_NAME_1]" in redacted
    assert "John" not in redacted
    assert BLOCKLIST_PLACEHOLDER in redacted

