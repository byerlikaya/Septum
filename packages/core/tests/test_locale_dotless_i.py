"""Dotted / dotless I folding in Turkic-language matching.

Turkish and Azerbaijani pair ``I`` with ``ı`` and ``İ`` with ``i``, while
all-caps text and ASCII keyboards routinely mix the two. In a Turkic
document every one of the four letters folds to ``i`` so an all-caps
mention ("SIRMA") and its title-case form ("Sırma") share a placeholder.
Other languages keep the plain ``str.lower`` behaviour, so a dotless ``ı``
stays distinct from ``i`` there.
"""

from __future__ import annotations

import pytest

from septum_core.anonymization_map import AnonymizationMap
from septum_core.text_utils import locale_lower, normalize_for_comparison


@pytest.mark.parametrize("language", ["tr", "az", "tr-TR"])
@pytest.mark.parametrize("form", ["SIRMA", "Sırma", "sırma", "SİRMA", "Sirma", "sirma"])
def test_turkic_languages_fold_every_i_variant_together(language: str, form: str) -> None:
    assert normalize_for_comparison(form, language) == "sirma"


@pytest.mark.parametrize("language", ["tr", "az"])
def test_turkic_lowercasing_pairs_dotted_and_dotless(language: str) -> None:
    assert locale_lower("İSTANBUL ISPARTA Iğdır", language) == "istanbul ısparta ığdır"


@pytest.mark.parametrize("language", ["tr", "az"])
def test_turkic_comparison_fold_keeps_the_length(language: str) -> None:
    text = "İSTANBUL ISPARTA Iğdır ılık"
    folded = normalize_for_comparison(text, language)
    assert folded == "istanbul isparta iğdir ilik"
    assert len(folded) == len(text)


@pytest.mark.parametrize("language", ["en", "de", "fr"])
def test_other_languages_keep_dotless_i_distinct(language: str) -> None:
    assert normalize_for_comparison("Sırma", language) == "sırma"
    assert normalize_for_comparison("SIRMA", language) == "sirma"
    assert normalize_for_comparison("Sırma", language) != normalize_for_comparison("SIRMA", language)


def test_all_caps_turkish_name_shares_the_placeholder() -> None:
    amap = AnonymizationMap(document_id=1, language="tr")
    title = amap.add_entity("Sırma Yılmaz", "PERSON_NAME")
    caps = amap.add_entity("SIRMA YILMAZ", "PERSON_NAME")
    assert caps == title


def test_dotted_capital_shares_the_placeholder() -> None:
    amap = AnonymizationMap(document_id=1, language="tr")
    title = amap.add_entity("İlknur Işık", "PERSON_NAME")
    caps = amap.add_entity("İLKNUR IŞIK", "PERSON_NAME")
    assert caps == title


def test_all_caps_turkish_mention_is_masked_by_the_blocklist() -> None:
    amap = AnonymizationMap(document_id=1, language="tr")
    placeholder = amap.add_entity("Sırma Yılmaz", "PERSON_NAME")
    redacted = amap.apply_blocklist("Toplantıya SIRMA YILMAZ katıldı.", "tr")
    assert "SIRMA" not in redacted
    assert "YILMAZ" not in redacted
    assert placeholder in redacted


def test_ascii_caps_identifier_in_turkish_text_matches_lowercase() -> None:
    amap = AnonymizationMap(document_id=1, language="tr")
    lower = amap.add_entity("info@example.com", "EMAIL_ADDRESS")
    upper = amap.add_entity("INFO@EXAMPLE.COM", "EMAIL_ADDRESS")
    assert upper == lower


def test_english_document_keeps_dotless_i_names_apart() -> None:
    amap = AnonymizationMap(document_id=1, language="en")
    dotless = amap.add_entity("Sırma", "PERSON_NAME")
    caps = amap.add_entity("SIRMA", "PERSON_NAME")
    assert caps != dotless
