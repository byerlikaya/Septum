"""Known-entity masking matches whole values only, never inside a word.

Regression for a chat turn whose document map held a one-letter EMPLOYEE_ID
fragment ("T") and an all-caps label registered as an entity: plain substring
matching turned every "T" of the context into a placeholder ("[EMPLOYEE_ID_1]arih").
"""

from __future__ import annotations

import re
import time

import pytest
from sanitizer_doubles import PatternSanitizer, literal
from septum_api.services import pii_gate
from septum_api.services.anonymization_map import AnonymizationMap

CONTRACT_TAIL = (
    "T.C. [NATIONAL_ID_2]\nTarih: [DATE_1]\n"
    "⚠ KVKK AYDINLATMA METNİ: Bu sözleşmede yer alan kişisel verileriniz"
)


def _contract_map() -> AnonymizationMap:
    amap = AnonymizationMap(document_id=1, language="tr")
    amap.add_entity("T", "EMPLOYEE_ID")
    amap.add_entity("47382910456", "NATIONAL_ID")
    amap.add_entity("TR94 0006 2000 1010 0007 1234 56", "IBAN")
    amap.add_entity("Ahmet Çelik", "PERSON_NAME")
    return amap


def _map_of(*pairs: tuple[str, str]) -> AnonymizationMap:
    amap = AnonymizationMap(document_id=1, language="tr")
    for original, entity in pairs:
        amap.add_entity(original, entity)
    return amap


def test_one_letter_original_is_never_matched() -> None:
    assert pii_gate.mask_known_entities(CONTRACT_TAIL, _contract_map()) == CONTRACT_TAIL


def test_one_letter_original_stays_resolvable() -> None:
    amap = _contract_map()
    pii_gate.mask_known_entities(CONTRACT_TAIL, amap)

    assert amap.entity_map["T"] == "[EMPLOYEE_ID_1]"


def test_short_letter_original_never_matches_in_the_middle_of_a_word() -> None:
    amap = AnonymizationMap(document_id=1, language="tr")
    amap.add_entity("KVKK", "ORGANIZATION_NAME")

    out = pii_gate.mask_known_entities("XKVKKLAR, KVKK AYDINLATMA, ‘KVKK’", amap)

    assert out == "XKVKKLAR, [ORGANIZATION_NAME_1] AYDINLATMA, ‘[ORGANIZATION_NAME_1]’"


@pytest.mark.parametrize(
    ("original", "text", "expected"),
    [
        ("Ahmet Yılmaz", "Ahmet Yılmazın evi", "[PERSON_NAME_1]ın evi"),
        ("Péter", "Péternek", "[PERSON_NAME_1]nek"),
        ("Liisa", "Liisalle", "[PERSON_NAME_1]lle"),
        ("Ahmet", "Ahmet123", "[PERSON_NAME_1]123"),
        ("Ann", "Annäherung", "[PERSON_NAME_1]äherung"),
        ("Çelik", "Çelikçi, Çelik’in", "[PERSON_NAME_1]çi, [PERSON_NAME_1]’in"),
    ],
)
def test_suffix_written_onto_a_letter_original_does_not_hide_it(
    original: str, text: str, expected: str
) -> None:
    amap = AnonymizationMap(document_id=1, language="tr")
    amap.add_entity(original, "PERSON_NAME")

    assert pii_gate.mask_known_entities(text, amap) == expected


@pytest.mark.parametrize(
    ("original", "entity", "text", "expected"),
    [
        (
            "TR330006100519786457841326",
            "IBAN",
            "IBANTR330006100519786457841326",
            "IBAN[IBAN_1]",
        ),
        ("A4711", "MEDICAL_RECORD_NUMBER", "XA4711", "X[MEDICAL_RECORD_NUMBER_1]"),
        ("A4711", "MEDICAL_RECORD_NUMBER", "A4711B", "[MEDICAL_RECORD_NUMBER_1]B"),
        ("A4711", "MEDICAL_RECORD_NUMBER", "A47110 A4711", "A47110 [MEDICAL_RECORD_NUMBER_1]"),
    ],
)
def test_original_with_a_digit_keeps_digit_boundaries_only(
    original: str, entity: str, text: str, expected: str
) -> None:
    amap = AnonymizationMap(document_id=1, language="en")
    amap.add_entity(original, entity)

    assert pii_gate.mask_known_entities(text, amap) == expected


def test_single_unspaced_glyph_original_masks() -> None:
    amap = AnonymizationMap(document_id=1, language="zh")
    amap.add_entity("明", "PERSON_NAME")

    assert pii_gate.mask_known_entities("小明来了", amap) == "小[PERSON_NAME_1]来了"


def test_all_caps_abbreviation_masks_at_a_word_start_only() -> None:
    amap = AnonymizationMap(document_id=1, language="en")
    amap.add_entity("ST.", "STREET_ADDRESS")

    out = pii_gate.mask_known_entities("MAIN ST. 5, BIGST. 6", amap)

    assert out == "MAIN [STREET_ADDRESS_1] 5, BIGST. 6"


@pytest.mark.parametrize(
    ("original", "entity", "text", "expected"),
    [
        (
            "Acme Lojistik",
            "ORGANIZATION_NAME",
            "FİRMA ÜNVANIAcme Lojistik A.Ş.",
            "FİRMA ÜNVANI[ORGANIZATION_NAME_1] A.Ş.",
        ),
        ("Titan", "PROJECT_TITAN", "kod adı ProjeTitan", "kod adı Proje[PROJECT_TITAN_1]"),
        ("Ahmet Çelik", "PERSON_NAME", "Sicil 4711Ahmet Çelik", "Sicil 4711[PERSON_NAME_1]"),
        ("St.", "STREET_ADDRESS", "Main St. 5, BigSt. 6", "Main [STREET_ADDRESS_1] 5, Big[STREET_ADDRESS_1] 6"),
    ],
)
def test_title_case_original_glued_to_the_previous_word_masks(
    original: str, entity: str, text: str, expected: str
) -> None:
    amap = AnonymizationMap(document_id=1, language="tr")
    amap.add_entity(original, entity)

    assert pii_gate.mask_known_entities(text, amap) == expected


def test_short_original_ending_a_word_is_masked_there() -> None:
    """Accepted over-mask: a short value that ends a longer word is masked in it."""
    amap = AnonymizationMap(document_id=1, language="tr")
    amap.add_entity("KVKK", "ORGANIZATION_NAME")
    amap.add_entity("kvkk", "ORGANIZATION_NAME")

    out = pii_gate.mask_known_entities("XKVKK, xkvkk, XKVKKLAR, xkvkklar", amap)

    assert out == "X[ORGANIZATION_NAME_1], x[ORGANIZATION_NAME_1], XKVKKLAR, xkvkklar"


def test_longest_original_wins_across_title_case_and_all_caps_sets() -> None:
    amap = AnonymizationMap(document_id=1, language="en")
    amap.add_entity("AC", "ORGANIZATION")
    amap.add_entity("ACme Corp", "ORGANIZATION")

    out = pii_gate.mask_known_entities("ACme Corp, AC, XACme Corp", amap)

    assert out == "[ORGANIZATION_2], [ORGANIZATION_1], X[ORGANIZATION_2]"


def test_longest_original_still_wins_across_digit_and_letter_sets() -> None:
    amap = AnonymizationMap(document_id=1, language="en")
    amap.add_entity("Acme", "ORGANIZATION")
    amap.add_entity("Acme 2000 Ltd", "ORGANIZATION")
    amap.add_entity("Acme Holdings", "ORGANIZATION")

    out = pii_gate.mask_known_entities("Acme 2000 Ltd, Acme Holdings, Acme", amap)

    assert out == "[ORGANIZATION_2], [ORGANIZATION_3], [ORGANIZATION_1]"


def test_name_written_against_an_unspaced_script_still_masks() -> None:
    amap = AnonymizationMap(document_id=1, language="zh")
    amap.add_entity("王芳", "PERSON_NAME")
    amap.add_entity("Wang", "PERSON_NAME")

    out = pii_gate.mask_known_entities("请联系王芳获取, 联系Wang获取", amap)

    assert out == "请联系[PERSON_NAME_1]获取, 联系[PERSON_NAME_2]获取"


def test_original_with_non_word_edges_needs_no_boundary() -> None:
    amap = AnonymizationMap(document_id=1, language="en")
    amap.add_entity("(Ann)", "PERSON_NAME")

    assert pii_gate.mask_known_entities("x(Ann)y", amap) == "x[PERSON_NAME_1]y"


def test_one_letter_value_is_masked_where_detected_but_never_searched() -> None:
    amap = AnonymizationMap(document_id=1, language="tr")
    amap.add_entity("47382910456", "NATIONAL_ID")
    sanitizer = PatternSanitizer(literal("T", "EMPLOYEE_ID"))

    out = pii_gate._gate_one("Sicil: T", amap, sanitizer, "tr")

    assert out == "Sicil: [EMPLOYEE_ID_1]"
    assert amap.entity_map["T"] == "[EMPLOYEE_ID_1]"
    assert "t" not in amap.token_to_placeholder
    assert pii_gate.mask_known_entities(CONTRACT_TAIL, amap) == CONTRACT_TAIL


def test_one_letter_values_a_recognizer_finds_are_masked() -> None:
    sanitizer = PatternSanitizer(
        (re.compile(r"Kan grubu: ([ABO]{1,2}[+-])"), "BLOOD_GROUP"),
        (re.compile(r"Cinsiyet: ([EK])\b"), "SEX"),
    )
    amap = AnonymizationMap(document_id=1, language="tr")

    out = pii_gate._gate_one(
        "Kan grubu: A+ ; Kan grubu: AB- ; Cinsiyet: K", amap, sanitizer, "tr"
    )

    assert out == "Kan grubu: [BLOOD_GROUP_1] ; Kan grubu: [BLOOD_GROUP_2] ; Cinsiyet: [SEX_1]"
    assert pii_gate.mask_known_entities("KAN A+ K", amap) == "KAN [BLOOD_GROUP_1] [SEX_1]"


def test_is_letter_fragment() -> None:
    assert pii_gate.is_letter_fragment("T")
    assert pii_gate.is_letter_fragment("T.")
    assert not pii_gate.is_letter_fragment("TC")
    assert not pii_gate.is_letter_fragment("1")
    assert not pii_gate.is_letter_fragment("王")
    assert not pii_gate.is_letter_fragment("김")
    assert not pii_gate.is_letter_fragment("A+")


def test_one_syllable_original_of_an_uncased_script_masks() -> None:
    amap = AnonymizationMap(document_id=1, language="ko")
    amap.add_entity("김", "PERSON_NAME")

    assert pii_gate.mask_known_entities("김 과장", amap) == "[PERSON_NAME_1] 과장"


@pytest.mark.parametrize(
    ("original", "entity", "text", "expected"),
    [
        (
            "ACME LOJİSTİK",
            "ORGANIZATION_NAME",
            "ÜNVANIACME LOJİSTİK A.Ş.",
            "ÜNVANI[ORGANIZATION_NAME_1] A.Ş.",
        ),
        ("AHMET ÇELİK", "PERSON_NAME", "ADI SOYADIAHMET ÇELİK", "ADI SOYADI[PERSON_NAME_1]"),
        ("AHMET ÇELİK", "PERSON_NAME", "4711AHMET ÇELİK", "4711[PERSON_NAME_1]"),
        ("AHMET ÇELİK", "PERSON_NAME", "ADI_AHMET ÇELİK", "ADI_[PERSON_NAME_1]"),
        ("ahmet çelik", "PERSON_NAME", "adsoyadahmet çelik", "adsoyad[PERSON_NAME_1]"),
        ("ANKARALI", "PERSON_NAME", "SOYADIANKARALI", "SOYADI[PERSON_NAME_1]"),
    ],
)
def test_multi_word_or_long_original_glued_on_the_left_masks(
    original: str, entity: str, text: str, expected: str
) -> None:
    amap = AnonymizationMap(document_id=1, language="tr")
    amap.add_entity(original, entity)

    assert pii_gate.mask_known_entities(text, amap) == expected


def test_short_single_tokens_still_need_a_word_start() -> None:
    amap = AnonymizationMap(document_id=1, language="tr")
    amap.add_entity("KVKK", "ORGANIZATION_NAME")
    amap.add_entity("ST.", "STREET_ADDRESS")
    amap.add_entity("T", "EMPLOYEE_ID")

    out = pii_gate.mask_known_entities("XKVKKLAR BIGST. AYDINLATMA T.C. KVKK", amap)

    assert out == "XKVKKLAR BIGST. AYDINLATMA T.C. [ORGANIZATION_NAME_1]"


def test_longest_original_wins_across_open_and_edged_sets() -> None:
    amap = AnonymizationMap(document_id=1, language="tr")
    amap.add_entity("ACME", "ORGANIZATION_NAME")
    amap.add_entity("ACME LOJİSTİK", "ORGANIZATION_NAME")

    out = pii_gate.mask_known_entities("ACME LOJİSTİK ve ACME", amap)

    assert out == "[ORGANIZATION_NAME_2] ve [ORGANIZATION_NAME_1]"


def test_symbol_fragment_masks_as_a_whole_token_only() -> None:
    amap = AnonymizationMap(document_id=1, language="tr")
    amap.add_entity("A+", "BLOOD_GROUP")
    amap.add_entity("O-", "BLOOD_GROUP")
    amap.add_entity("Ahmet Çelik", "PERSON_NAME")

    out = pii_gate.mask_known_entities("Ahmet Çelik | A+ | 1985; DATA+ ANA ALAN; kan: O-", amap)

    assert out == "[PERSON_NAME_1] | [BLOOD_GROUP_1] | 1985; DATA+ ANA ALAN; kan: [BLOOD_GROUP_2]"


def test_five_letter_single_token_opens_on_the_left_four_does_not() -> None:
    amap = _map_of(("AHMET", "PERSON_NAME"), ("KVKK", "ORGANIZATION_NAME"))

    out = pii_gate.mask_known_entities("BELGEAHMETLER XKVKKLAR", amap)

    assert out == "BELGE[PERSON_NAME_1]LER XKVKKLAR"


def test_short_multi_word_original_opens_on_the_left() -> None:
    amap = _map_of(("AB CD", "ORGANIZATION_NAME"))

    assert pii_gate.mask_known_entities("XAB CD", amap) == "X[ORGANIZATION_NAME_1]"


def test_symbol_fragment_followed_by_a_word_character_is_left_alone() -> None:
    amap = _map_of(("A+", "BLOOD_GROUP"))

    assert pii_gate.mask_known_entities("A+B testi, A+", amap) == "A+B testi, [BLOOD_GROUP_1]"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("SOYADIKAYA olarak geçer", "SOYADI[PERSON_NAME_1] olarak geçer"),
        ("ALICIALİ FATURA 1200 TL", "ALICI[PERSON_NAME_2] FATURA 1200 TL"),
        ("MÜŞTERİ ÜNVANIACME TOPLAM", "MÜŞTERİ ÜNVANI[ORGANIZATION_NAME_1] TOPLAM"),
        ("imzaKAYA", "imza[PERSON_NAME_1]"),
        ("notKAYA", "not[PERSON_NAME_1]"),
        ("imza:KAYA", "imza:[PERSON_NAME_1]"),
        ("adıkaya", "adı[PERSON_NAME_1]"),
    ],
)
def test_short_value_glued_to_a_label_or_a_case_step_masks(text: str, expected: str) -> None:
    amap = _map_of(
        ("KAYA", "PERSON_NAME"),
        ("ALİ", "PERSON_NAME"),
        ("kaya", "PERSON_NAME"),
        ("ACME", "ORGANIZATION_NAME"),
    )

    assert pii_gate.mask_known_entities(text, amap) == expected


def test_short_value_ends_a_word_or_stays() -> None:
    """Accepted over-mask at a word end ("VALİ"); the middle of a word stays."""
    amap = _map_of(("ALİ", "PERSON_NAME"), ("KVKK", "ORGANIZATION_NAME"), ("T", "EMPLOYEE_ID"))

    out = pii_gate.mask_known_entities("VALİ VALİLER XKVKKLAR AYDINLATMA T.C. ALİ", amap)

    assert out == "V[PERSON_NAME_1] VALİLER XKVKKLAR AYDINLATMA T.C. [PERSON_NAME_1]"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("SOYADKAYA", "SOYAD[PERSON_NAME_1]"),
        ("MÜŞTERİSOYADIKAYA", "MÜŞTERİSOYADI[PERSON_NAME_1]"),
        ("4471KAYA", "4471[PERSON_NAME_1]"),
        ("SOYADI_KAYA", "SOYADI_[PERSON_NAME_1]"),
        ("TC12345678901KAYA", "TC12345678901[PERSON_NAME_1]"),
        ("SOYADIKAYAADIALİ", "SOYADI[PERSON_NAME_1]ADI[PERSON_NAME_2]"),
        ("XKAYALAR", "XKAYALAR"),
        ("XALİB", "XALİB"),
    ],
)
def test_short_value_glued_after_any_label_masks_where_it_ends_its_word(
    text: str, expected: str
) -> None:
    amap = _map_of(("KAYA", "PERSON_NAME"), ("ALİ", "PERSON_NAME"))

    assert pii_gate.mask_known_entities(text, amap) == expected


def test_value_that_starts_a_label_does_not_split_the_label() -> None:
    amap = _map_of(("ALI", "PERSON_NAME"))

    assert pii_gate.mask_known_entities("ALICIALI", amap) == "ALICI[PERSON_NAME_1]"


def test_label_that_is_itself_an_original_is_masked_with_its_value() -> None:
    amap = _map_of(("ADI", "PERSON_NAME"), ("KAYA", "PERSON_NAME"))
    state = pii_gate._MaskState(amap)

    assert pii_gate._mask_known_originals("ADIKAYA", state) == "[PERSON_NAME_1][PERSON_NAME_2]"
    assert pii_gate.mask_known_entities("ADIKAYA", amap) == "[PERSON_NAME_1][PERSON_NAME_2]"


def test_label_inside_a_longer_word_is_no_cue() -> None:
    amap = _map_of(("KAYA", "PERSON_NAME"))

    assert pii_gate.mask_known_entities("XNAMEKAYALAR", amap) == "XNAMEKAYALAR"


def test_bare_letter_standing_alone_masks_in_any_text() -> None:
    amap = _map_of(("K", "SEX"), ("T", "EMPLOYEE_ID"))

    out = pii_gate.mask_known_entities("Cinsiyet: K; T.C. AYDINLATMA KAYIT", amap)

    assert out == "Cinsiyet: [SEX_1]; T.C. AYDINLATMA KAYIT"


def test_gate_masks_a_bare_letter_standing_alone() -> None:
    amap = _map_of(("K", "GENDER"))

    assert pii_gate._gate_one("Cinsiyet: K", amap, PatternSanitizer(), "tr") == (
        "Cinsiyet: [GENDER_1]"
    )


def test_padded_long_original_keeps_word_end_matching_fast() -> None:
    amap = _map_of(("KAYA", "PERSON_NAME"), ("A" + "_" * 5000 + "B", "ORGANIZATION_NAME"))
    state = pii_gate._MaskState(amap)
    pii_gate._mask_known_originals("warm", state)

    started = time.perf_counter()
    for text in ("x" * 200_000, "_" * 200_000, "A_" * 100_000):
        pii_gate._mask_known_originals(text, state)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5


@pytest.mark.parametrize("text", ["SOYADKAYA1990", "MÜŞTERİSOYADIKAYAFATURA"])
def test_short_value_glued_on_both_sides_without_a_label_stays_by_design(text: str) -> None:
    """Accepted trade-off: inside a word, with no label at a word start, a short value is not found."""
    amap = _map_of(("KAYA", "PERSON_NAME"))

    assert pii_gate.mask_known_entities(text, amap) == text


def test_title_case_value_glued_after_a_digit_with_a_suffix_masks() -> None:
    amap = _map_of(("Acme", "ORGANIZATION_NAME"))

    assert pii_gate.mask_known_entities("4711Acmeler", amap) == "4711[ORGANIZATION_NAME_1]ler"


def test_short_value_at_a_case_step_masks_with_a_suffix() -> None:
    amap = _map_of(("KAYA", "PERSON_NAME"))

    assert pii_gate.mask_known_entities("ekipKAYAnın", amap) == "ekip[PERSON_NAME_1]nın"


def test_initial_and_name_beat_the_bare_initial() -> None:
    amap = _map_of(("A.", "PERSON_NAME"), ("A. Yılmaz", "PERSON_NAME"))

    out = pii_gate.mask_known_entities("Kiracı A. Yılmaz", amap)

    assert out == f"Kiracı {amap.entity_map['A. Yılmaz']}"


def test_short_value_ending_a_word_before_a_particle_masks() -> None:
    amap = _map_of(("KAYA", "PERSON_NAME"))

    assert pii_gate.mask_known_entities("SOYADKAYA입니다", amap) == "SOYAD[PERSON_NAME_1]입니다"


def test_dotted_short_value_inside_a_word_stays() -> None:
    amap = _map_of(("ST.", "STREET_ADDRESS"))

    assert pii_gate.mask_known_entities("XST.A", amap) == "XST.A"


def test_longer_dotted_value_wins_over_its_word_at_a_token_start() -> None:
    amap = _map_of(("ST", "STREET_ADDRESS"), ("ST.", "STREET_ADDRESS"))

    assert pii_gate.mask_known_entities("ST. 5", amap) == "[STREET_ADDRESS_2] 5"


def test_gate_initial_and_name_beat_the_bare_initial() -> None:
    amap = _map_of(("A.", "PERSON_NAME"), ("A. Yılmaz", "PERSON_NAME"))

    out = pii_gate._gate_one("Kiracı A. Yılmaz", amap, PatternSanitizer(), "tr")

    assert out == f"Kiracı {amap.entity_map['A. Yılmaz']}"


def test_short_multi_word_lowercase_value_glued_masks() -> None:
    amap = _map_of(("ab c", "PERSON_NAME"))

    assert pii_gate.mask_known_entities("xab c", amap) == "x[PERSON_NAME_1]"
