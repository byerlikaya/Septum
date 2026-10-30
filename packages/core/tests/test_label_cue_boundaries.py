"""Label cues keep their context in field names, plurals and compounds; value spans stay exact.

A short abbreviation cue ("SSN", "EIN", "DOB") must not match inside an
unrelated word ("protein", "Adobe"), yet it still marks the identifier when it
is part of a snake_case or camelCase field name, carries a plural "s" or sits
at the end of a compound ("Newborn"). Every case that masked the identifier
before the script-aware boundaries must keep masking it.
"""

from __future__ import annotations

import importlib
import logging

import pytest
from septum_core import SeptumCoreConfig, SeptumEngine
from septum_core.anonymization_map import AnonymizationMap
from septum_core.detector import (
    CalendarDateRecognizer,
    CPFRecognizer,
    DriversLicenseRecognizer,
    LicensePlateRecognizer,
    SSNRecognizer,
    StructuralAddressRecognizer,
    TaxIDRecognizer,
)
from septum_core.ports import NullSemanticDetectionPort
from septum_core.recognizers import BUILTIN_REGULATION_IDS
from septum_core.recognizers.registry import RecognizerRegistry

_CONFIG = SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=False)
_ENGINES: dict[tuple[str, ...], SeptumEngine] = {}


def _engine(regulations: tuple[str, ...]) -> SeptumEngine:
    if regulations not in _ENGINES:
        _ENGINES[regulations] = SeptumEngine(
            regulations=list(regulations), config=_CONFIG, semantic_port=NullSemanticDetectionPort()
        )
    return _ENGINES[regulations]


def _detected(regulations: tuple[str, ...], text: str) -> list[tuple[str, str]]:
    logging.disable(logging.CRITICAL)
    try:
        result = _engine(regulations)._detector.sanitize(
            text, "en", AnonymizationMap(document_id=0, language="en")
        )
    finally:
        logging.disable(logging.NOTSET)
    return [(s.entity_type, text[s.start : s.end]) for s in result.detected_spans]


def _found(recognizers, text: str, entity: str) -> list[str]:
    return sorted(
        {
            text[r.start : r.end]
            for recognizer in recognizers
            for r in recognizer.analyze(text, [entity])
            if r.entity_type == entity
        }
    )


def _pack(regulation: str) -> list:
    return importlib.import_module(f"septum_core.recognizers.{regulation}.recognizers").get_recognizers()


# ── End to end: every case masked on the previous release stays masked ──────

_E2E = [
    (("gdpr",), "Employer FEIN: 12-3456789", "TAX_ID", ["12-3456789"]),
    (("kvkk",), "Employer FEIN: 12-3456789", "TAX_ID", ["12-3456789"]),
    (("gdpr",), "ITIN 912701234 on file", "TAX_ID", ["912701234"]),
    (("gdpr",), '{"employer_ein": "12-3456789"}', "TAX_ID", ["12-3456789"]),
    (("gdpr",), "USt-Identifikationsnummer: DE298431076", "TAX_ID", ["DE298431076"]),
    (("gdpr",), "USt-Identifikationsnummer: 27/431/07892", "TAX_ID", ["27/431/07892"]),
    (("gdpr",), "NIFs: B86432178", "TAX_ID", ["B86432178"]),
    (("ccpa",), "SSNs: 123-45-6789", "SOCIAL_SECURITY_NUMBER", ["123-45-6789"]),
    (("hipaa",), "customer_ssn: 123-45-6789", "SOCIAL_SECURITY_NUMBER", ["123-45-6789"]),
    (("hipaa",), '{"patientSSN": "123-45-6789"}', "SOCIAL_SECURITY_NUMBER", ["123-45-6789"]),
    (("ccpa",), "ssn_number=123-45-6789", "SOCIAL_SECURITY_NUMBER", ["123-45-6789"]),
    (("ccpa",), "CDL: 123456789", "DRIVERS_LICENSE", ["123456789"]),
    (("ccpa",), "DLN 123456789", "DRIVERS_LICENSE", ["123456789"]),
    (("ccpa",), '{"dl_number": "98765432"}', "DRIVERS_LICENSE", ["98765432"]),
    (("gdpr",), '{"patient_dob": "1990-01-02"}', "DATE_OF_BIRTH", ["1990-01-02"]),
    (("gdpr",), "patientDOB=02/03/1990", "DATE_OF_BIRTH", ["02/03/1990"]),
    (("ccpa",), "Newborn 01/02/2024, 3.2 kg", "DATE_OF_BIRTH", ["01/02/2024"]),
    (("kvkk",), "Firstborn 1990-05-04", "DATE_OF_BIRTH", ["1990-05-04"]),
    (("gdpr",), "DOBs: 01/02/1990, 03/04/1992", "DATE_OF_BIRTH", ["01/02/1990", "03/04/1992"]),
]


@pytest.mark.parametrize(("regulations", "text", "entity", "values"), _E2E)
def test_cued_identifier_is_masked_end_to_end(
    regulations: tuple[str, ...], text: str, entity: str, values: list[str]
) -> None:
    found = sorted(value for kind, value in _detected(regulations, text) if kind == entity)
    assert found == values


# ── Recognizer level: field names, plurals, prefixed abbreviations ──────────


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("EINs: 12-3456789", "12-3456789"),
        ("TINs: 12-3456789", "12-3456789"),
        ("SIRETs: 12-3456789", "12-3456789"),
        ("tax_tin: 12-3456789", "12-3456789"),
        ("employerEIN: 12-3456789", "12-3456789"),
        ("einValue=12-3456789", "12-3456789"),
        ("ИНН 7707083893", "7707083893"),
        ("ИННа 7707083893", "7707083893"),
        ("USt-Identifikationsnummer: DE298431076", "DE298431076"),
    ],
)
def test_tax_cue_in_field_names_plurals_and_inflections(text: str, value: str) -> None:
    assert _found([TaxIDRecognizer()], text, "TAX_ID") == [value]


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("DLs: A1234567", "A1234567"),
        ("OLN: A1234567", "A1234567"),
        ("CDL# A1234567", "A1234567"),
        ("driver_dl=A1234567", "A1234567"),
    ],
)
def test_driving_licence_cue_variants(text: str, value: str) -> None:
    assert _found([DriversLicenseRecognizer()], text, "DRIVERS_LICENSE") == [value]


@pytest.mark.parametrize(
    "text",
    ["protein 123456789", "specific 12-3456789", "unified 12-3456789", "Steinway 12-3456789"],
)
def test_tax_cue_inside_an_unrelated_word_still_does_not_match(text: str) -> None:
    assert _found([TaxIDRecognizer()], text, "TAX_ID") == []


@pytest.mark.parametrize(
    ("text", "entity"),
    [
        ("Adobe 12/03/1990", "DATE"),
        ("Osborne 12/03/1990", "DATE"),
        ("Stubborn 12/03/1990", "DATE_OF_BIRTH"),
        ("doc_dob 12/03/1990", "DATE_OF_BIRTH"),
    ],
)
def test_birth_cue_edges(text: str, entity: str) -> None:
    results = CalendarDateRecognizer().analyze(text, ["DATE", "DATE_OF_BIRTH"])
    assert [r.entity_type for r in results] == [entity]


def test_word_cue_compound_still_matches() -> None:
    assert _found([DriversLicenseRecognizer()], "Führerscheinnummer B072RRE2I55", "DRIVERS_LICENSE") == [
        "B072RRE2I55"
    ]


@pytest.mark.parametrize(
    "text",
    ["ПриветSSN 123-45-6789", "١٢SSN 123-45-6789", "²SSN 123-45-6789"],
)
def test_ssn_cue_glued_to_another_script_or_a_digit_still_matches(text: str) -> None:
    assert _found([SSNRecognizer()], text, "SOCIAL_SECURITY_NUMBER") == ["123-45-6789"]


def test_ssn_cue_does_not_match_inside_a_word() -> None:
    assert _found([SSNRecognizer()], "lessons 123-45-6789", "SOCIAL_SECURITY_NUMBER") == []


# ── All-caps text: a cue may start or end anywhere inside a capital run ─────


@pytest.mark.parametrize(
    ("recognizer", "entity", "text", "value"),
    [
        (SSNRecognizer, "SOCIAL_SECURITY_NUMBER", "PATIENTSSN 123-45-6789", "123-45-6789"),
        (SSNRecognizer, "SOCIAL_SECURITY_NUMBER", "SSNNO 123-45-6789", "123-45-6789"),
        (TaxIDRecognizer, "TAX_ID", "EMPLOYEREIN 12-3456789", "12-3456789"),
        (TaxIDRecognizer, "TAX_ID", "ПриветEMPLOYEREIN 12-3456789", "12-3456789"),
    ],
)
def test_all_caps_compound_cue_gives_context(recognizer, entity: str, text: str, value: str) -> None:
    assert _found([recognizer()], text, entity) == [value]


def test_all_caps_birth_cue_compound_gives_context() -> None:
    results = CalendarDateRecognizer().analyze("CUSTOMERDOB 1990-01-02", ["DATE", "DATE_OF_BIRTH"])
    assert [r.entity_type for r in results] == ["DATE_OF_BIRTH"]


@pytest.mark.parametrize(
    ("regulations", "text", "entity", "value"),
    [
        (("hipaa",), "PATIENTSSN 123-45-6789", "SOCIAL_SECURITY_NUMBER", "123-45-6789"),
        (("gdpr",), "EMPLOYEREIN 12-3456789", "TAX_ID", "12-3456789"),
        (("gdpr",), "CUSTOMERDOB 1990-01-02", "DATE_OF_BIRTH", "1990-01-02"),
    ],
)
def test_all_caps_compound_cue_end_to_end(
    regulations: tuple[str, ...], text: str, entity: str, value: str
) -> None:
    assert (entity, value) in _detected(regulations, text)


def test_all_caps_text_accepts_over_masking() -> None:
    # Accepted trade-off: in all-caps text a cue inside a longer word counts,
    # so "PROTEIN" reads as an "EIN" label. The mixed-case word does not.
    assert _found([TaxIDRecognizer()], "PROTEIN 123456789", "TAX_ID") == ["123456789"]
    assert _found([TaxIDRecognizer()], "Protein 123456789", "TAX_ID") == []


# ── Cues of two letters or fewer need a whole word ──────────────────────────
# "ID", "NO", "DL" and "RG" are too short to be read inside a capital run or
# at a camelCase step: "NOVEMBER" or "PROVIDER" is no label. "_", a digit or
# a glued letter of a touching script is still an edge.


@pytest.mark.parametrize(
    "text",
    [
        "NOVEMBER 20241031",
        "AMOUNT PAID: 12500000",
        "PROVIDER 123456789",
        "ANNOUNCEMENT 12345678",
    ],
)
def test_short_cue_inside_an_all_caps_word_gives_no_national_id(text: str) -> None:
    assert _found(_pack("kvkk"), text, "NATIONAL_ID") == []


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("id: 12345678901", "12345678901"),
        ("no 12345678901", "12345678901"),
        ("ID: 12345678901", "12345678901"),
        ("customer_id: 12345678", "12345678"),
        ("ID号12345678", "12345678"),
        ("IDs 12345678", "12345678"),
    ],
)
def test_short_cue_as_a_whole_word_still_gives_context(text: str, value: str) -> None:
    assert value in _found(_pack("kvkk"), text, "NATIONAL_ID")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("DL: A1234567", ["A1234567"]),
        ("dl_number=A1234567", ["A1234567"]),
        ("DL号A1234567", ["A1234567"]),
        # Decision: the two-letter cue does not open a capital run or a camel
        # step. "DLNUMBER" still gives context through the three-letter "DLN"
        # cue, which keeps the caps step like every longer cue.
        ("DLNUMBER A1234567", ["A1234567"]),
        ("dlNumber A1234567", []),
        ("HANDLE A1234567", []),
    ],
)
def test_two_letter_driving_licence_cue_needs_a_whole_word(text: str, expected: list[str]) -> None:
    assert _found([DriversLicenseRecognizer()], text, "DRIVERS_LICENSE") == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("RG: 12.345.678-9", ["12.345.678-9"]), ("CARGO: 12.345.678-9", []), ("CARGO 12.345.678-9", [])],
)
def test_two_letter_civil_identity_cue_needs_a_whole_word(text: str, expected: list[str]) -> None:
    assert _found(_pack("lgpd"), text, "NATIONAL_ID") == expected


# ── Case steps in every cased script, not only ASCII ────────────────────────

_CASED_SCRIPT_CASES = [
    (TaxIDRecognizer, "TAX_ID", 'ИННФЛ="500100732259"', "500100732259"),
    (TaxIDRecognizer, "TAX_ID", 'ИННЮЛ="500100732259"', "500100732259"),
    (TaxIDRecognizer, "TAX_ID", 'ИННКонтрагента="500100732259"', "500100732259"),
    (TaxIDRecognizer, "TAX_ID", "КодИНН 500100732259", "500100732259"),
    (TaxIDRecognizer, "TAX_ID", "номерИНН 500100732259", "500100732259"),
    (TaxIDRecognizer, "TAX_ID", "ИННы 500100732259", "500100732259"),
    (SSNRecognizer, "SOCIAL_SECURITY_NUMBER", "ŞSSN 123-45-6789", "123-45-6789"),
    (SSNRecognizer, "SOCIAL_SECURITY_NUMBER", "İSSN 123-45-6789", "123-45-6789"),
]


@pytest.mark.parametrize(("recognizer", "entity", "text", "value"), _CASED_SCRIPT_CASES)
def test_case_steps_count_as_cue_edges_in_every_cased_script(
    recognizer, entity: str, text: str, value: str
) -> None:
    assert _found([recognizer()], text, entity) == [value]


@pytest.mark.parametrize(
    ("regulations", "text", "entity", "value"),
    [
        (("gdpr",), 'ИННФЛ="500100732259"', "TAX_ID", "500100732259"),
        (("gdpr",), 'ИННЮЛ="500100732259"', "TAX_ID", "500100732259"),
        (("kvkk",), 'ИННКонтрагента="500100732259"', "TAX_ID", "500100732259"),
        (("gdpr",), "КодИНН 500100732259", "TAX_ID", "500100732259"),
        (("gdpr",), "ИННы 500100732259", "TAX_ID", "500100732259"),
        (("hipaa",), "ŞSSN 123-45-6789", "SOCIAL_SECURITY_NUMBER", "123-45-6789"),
    ],
)
def test_case_steps_in_cased_scripts_end_to_end(
    regulations: tuple[str, ...], text: str, entity: str, value: str
) -> None:
    assert (entity, value) in _detected(regulations, text)


@pytest.mark.parametrize("text", ["длинный 7707083893", "Длинный 7707083893"])
def test_cyrillic_cue_inside_a_lowercase_word_is_no_cue(text: str) -> None:
    # "инн" inside "длинный" is not the ИНН label, as "ein" inside "protein" is not EIN.
    assert _found([TaxIDRecognizer()], text, "TAX_ID") == []


# ── Licence plate spans never end in a separator ────────────────────────────


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("Vehicle reg: AB 1234 , owner Ali", "AB 1234"),
        ("Kennzeichen: AB-1234-\nHalter: X", "AB-1234"),
        ("plate number XY 123\n\nNext line", "XY 123"),
        ("Plaka: AB 1234 - ok", "AB 1234"),
        ("plate number AB 1234 CD", "AB 1234 CD"),
    ],
)
def test_plate_span_has_no_trailing_separator(text: str, value: str) -> None:
    assert _found([LicensePlateRecognizer()], text, "LICENSE_PLATE") == [value]


def test_plate_sanitized_text_keeps_the_separator() -> None:
    text = "Kennzeichen: AB-1234-\nHalter: X"
    assert ("LICENSE_PLATE", "AB-1234") in _detected(("kvkk",), text)


# ── Address cues ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    ["住所APT号", "ที่อยู่APTครับ", "주소APT입니다", "العنوانAPTو"],
)
def test_address_cue_next_to_glued_script_has_its_exact_span(text: str) -> None:
    cue = StructuralAddressRecognizer()._structural_cue
    assert [m.group() for m in cue.finditer(text)] == ["APT"]


@pytest.mark.parametrize(("text", "expected"), [("APTLY NOTED", []), ("APT. 5", ["APT."]), ("Unit 4", ["Unit"])])
def test_address_cue_latin_guard(text: str, expected: list[str]) -> None:
    cue = StructuralAddressRecognizer()._structural_cue
    assert [m.group() for m in cue.finditer(text) if not any(c.isdigit() for c in m.group())] == expected


# ── Latin no-regression guards ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("recognizer", "entity", "text"),
    [
        (CPFRecognizer, "CPF", "x123.456.789-09"),
        (CPFRecognizer, "CPF", "123.456.789-09x"),
        (DriversLicenseRecognizer, "DRIVERS_LICENSE", "DL: A1234567x"),
        (LicensePlateRecognizer, "LICENSE_PLATE", "plate number xAB 1234"),
    ],
)
def test_baseline_value_glued_to_latin_stays_undetected(recognizer, entity: str, text: str) -> None:
    assert _found([recognizer()], text, entity) == []


_LATIN_PACK_GUARDS = [
    ("uk_gdpr", "NATIONAL_ID", "AB123456C"),
    ("pdpl_sa", "NATIONAL_ID", "1012345678"),
    ("nzpa", "TAX_ID", "49-091-850"),
    ("pdpa_th", "NATIONAL_ID", "1-2345-67890-12-3"),
    ("australia_pa", "TAX_ID", "123-456-782"),
    ("australia_pa", "HEALTH_INSURANCE_ID", "2123-45670-1"),
    ("popia", "NATIONAL_ID", "8001015009087"),
    ("ccpa", "DRIVERS_LICENSE", "A1234567"),
    ("cpra", "DRIVERS_LICENSE", "A1234567"),
    ("dpdp", "NATIONAL_ID", "2345 6789 0123"),
    ("pdpa_sg", "NATIONAL_ID", "S1234567D"),
    ("appi", "NATIONAL_ID", "1234-5678-9012"),
]


@pytest.mark.parametrize(("regulation", "entity", "value"), _LATIN_PACK_GUARDS)
def test_pack_value_glued_to_latin_stays_undetected(regulation: str, entity: str, value: str) -> None:
    recognizers = _pack(regulation)
    assert _found(recognizers, f"ref x{value}", entity) == []
    assert _found(recognizers, f"ref {value}x", entity) == []
    assert _found(recognizers, f"ref {value} ok", entity) == [value]


# ── A window of a longer number next to a prefix script ────────────────────


def test_twelve_digit_window_next_to_hebrew_matches_the_latin_behaviour() -> None:
    # The superscript glued to the card number is a word character, so the
    # card recognizers see no edge; the twelve-digit pack pattern reports the
    # same window as it does after a space. Without the glued superscript the
    # whole card is masked.
    appi = _pack("appi")
    hebrew = "שלום4111-1111-1111-1111²"
    latin = "x 4111-1111-1111-1111²"
    assert _found(appi, hebrew, "NATIONAL_ID") == _found(appi, latin, "NATIONAL_ID") == ["4111-1111-1111"]
    all_packs = tuple(BUILTIN_REGULATION_IDS)
    card = "שלום4111-1111-1111-1111"
    assert any(
        kind == "CREDIT_CARD_NUMBER" and "4111-1111-1111-1111" in value
        for kind, value in _detected(all_packs, card)
    )


# ── Custom keyword recognizer ───────────────────────────────────────────────


def _keyword_matches(keywords: list[str], text: str) -> list[str]:
    from types import SimpleNamespace

    custom = SimpleNamespace(
        id=1, name="codenames", entity_type="CODENAME", detection_method="keyword_list",
        pattern=None, keywords=keywords, llm_prompt=None, context_words=[],
        placeholder_label="CODENAME", is_active=True,
    )
    recognizer = RecognizerRegistry._build_keyword_recognizer(custom)
    return [text[m.start : m.end] for m in recognizer.analyze(text=text, entities=["CODENAME"], nlp_artifacts=None)]


@pytest.mark.parametrize(
    ("keywords", "text", "expected"),
    [
        (["机密"], "这是机密文件", ["机密"]),
        (["ProjectX"], "关于ProjectX的计划", ["ProjectX"]),
        (["ProjectX"], "프로젝트ProjectX입니다", ["ProjectX"]),
        (["cat"], "The cat sat on the catalog.", ["cat"]),
        (["cat"], "concat", []),
    ],
)
def test_custom_keyword_matches_next_to_glued_script(keywords: list[str], text: str, expected: list[str]) -> None:
    assert _keyword_matches(keywords, text) == expected
