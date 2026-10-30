"""Identifiers written straight against text of a script that joins its words without spaces.

Han, kana and Thai put no space between words, Hangul writes particles onto
the token before them and Arabic writes prefixes onto the token after them, so
a phone number, IBAN or ID number can touch letters of those scripts. Each
such identifier must be found with exactly its own span. In space-delimited
text nothing changes: a digit run inside a longer alphanumeric word is still
not an identifier, and a short label cue never matches inside a longer word.
"""

from __future__ import annotations

import importlib
import re

import pytest
from septum_core import SeptumCoreConfig, SeptumEngine
from septum_core.anonymization_map import AnonymizationMap
from septum_core.detector import (
    CalendarDateRecognizer,
    CPFRecognizer,
    CreditCardNumberRecognizer,
    DriversLicenseRecognizer,
    ExtendedPhoneRecognizer,
    LicensePlateRecognizer,
    MACAddressRecognizer,
    PassportNumberRecognizer,
    SSNRecognizer,
    StructuralAddressRecognizer,
    TaxIDRecognizer,
    ValidatedIBANRecognizer,
)
from septum_core.ports import NullSemanticDetectionPort
from septum_core.text_boundaries import token_bounded


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
    module = importlib.import_module(f"septum_core.recognizers.{regulation}.recognizers")
    return module.get_recognizers()


_BASELINE_CASES = [
    # (recognizer, entity, text, value)
    (ExtendedPhoneRecognizer, "PHONE_NUMBER", "电话138-1234-5678请回电", "138-1234-5678"),
    (ExtendedPhoneRecognizer, "PHONE_NUMBER", "전화010-1234-5678입니다", "010-1234-5678"),
    (ExtendedPhoneRecognizer, "PHONE_NUMBER", "โทร081-234-5678ครับ", "081-234-5678"),
    (ExtendedPhoneRecognizer, "PHONE_NUMBER", "الهاتف050-123-4567", "050-123-4567"),
    (ExtendedPhoneRecognizer, "PHONE_NUMBER", "電話は090-1234-5678です", "090-1234-5678"),
    (
        ValidatedIBANRecognizer,
        "IBAN",
        "账户TR330006100519786457841326转账",
        "TR330006100519786457841326",
    ),
    (
        ValidatedIBANRecognizer,
        "IBAN",
        "口座DE89370400440532013000へ",
        "DE89370400440532013000",
    ),
    (CreditCardNumberRecognizer, "CREDIT_CARD_NUMBER", "卡号4111111111111111请", "4111111111111111"),
    (CreditCardNumberRecognizer, "CREDIT_CARD_NUMBER", "카드4111-1111-1111-1111입니다", "4111-1111-1111-1111"),
    (MACAddressRecognizer, "MAC_ADDRESS", "地址00:1A:2B:3C:4D:5E的设备", "00:1A:2B:3C:4D:5E"),
    (SSNRecognizer, "SOCIAL_SECURITY_NUMBER", "SSN 号123-45-6789码", "123-45-6789"),
    (CPFRecognizer, "CPF", "号码123.456.789-09的", "123.456.789-09"),
    (PassportNumberRecognizer, "PASSPORT_NUMBER", "护照E12345678", "E12345678"),
    (PassportNumberRecognizer, "PASSPORT_NUMBER", "パスポートTK1234567です", "TK1234567"),
    (DriversLicenseRecognizer, "DRIVERS_LICENSE", "驾照 DL号A1234567的", "A1234567"),
    (TaxIDRecognizer, "TAX_ID", "税号12-3456789的", "12-3456789"),
    (TaxIDRecognizer, "TAX_ID", "ИНН 税号DE298431076号", "DE298431076"),
    (LicensePlateRecognizer, "LICENSE_PLATE", "车牌 号34ABC123的", "34ABC123"),
    # Arabic, Thai and Hangul neighbours.
    (ValidatedIBANRecognizer, "IBAN", "الحسابDE89370400440532013000", "DE89370400440532013000"),
    (ValidatedIBANRecognizer, "IBAN", "บัญชีDE89370400440532013000ครับ", "DE89370400440532013000"),
    (ValidatedIBANRecognizer, "IBAN", "계좌DE89370400440532013000입니다", "DE89370400440532013000"),
    (MACAddressRecognizer, "MAC_ADDRESS", "العنوان00:1A:2B:3C:4D:5E", "00:1A:2B:3C:4D:5E"),
    (MACAddressRecognizer, "MAC_ADDRESS", "ที่อยู่00:1A:2B:3C:4D:5Eครับ", "00:1A:2B:3C:4D:5E"),
    (MACAddressRecognizer, "MAC_ADDRESS", "주소00:1A:2B:3C:4D:5E입니다", "00:1A:2B:3C:4D:5E"),
    (SSNRecognizer, "SOCIAL_SECURITY_NUMBER", "SSN رقم123-45-6789", "123-45-6789"),
    (SSNRecognizer, "SOCIAL_SECURITY_NUMBER", "SSN เลข123-45-6789ครับ", "123-45-6789"),
    (SSNRecognizer, "SOCIAL_SECURITY_NUMBER", "SSN 번호123-45-6789입니다", "123-45-6789"),
    (CPFRecognizer, "CPF", "رقم123.456.789-09", "123.456.789-09"),
    (CPFRecognizer, "CPF", "เลข123.456.789-09ครับ", "123.456.789-09"),
    (CPFRecognizer, "CPF", "번호123.456.789-09입니다", "123.456.789-09"),
    (PassportNumberRecognizer, "PASSPORT_NUMBER", "جواز سفرE12345678", "E12345678"),
    (PassportNumberRecognizer, "PASSPORT_NUMBER", "passport เลขE12345678ครับ", "E12345678"),
    (PassportNumberRecognizer, "PASSPORT_NUMBER", "passport 번호E12345678입니다", "E12345678"),
    (DriversLicenseRecognizer, "DRIVERS_LICENSE", "DL رقمA1234567", "A1234567"),
    (DriversLicenseRecognizer, "DRIVERS_LICENSE", "DL เลขA1234567ครับ", "A1234567"),
    (DriversLicenseRecognizer, "DRIVERS_LICENSE", "DL 번호A1234567입니다", "A1234567"),
    (TaxIDRecognizer, "TAX_ID", "الرقم الضريبي12-3456789", "12-3456789"),
    (TaxIDRecognizer, "TAX_ID", "tax id เลข12-3456789ครับ", "12-3456789"),
    (TaxIDRecognizer, "TAX_ID", "tax id 번호12-3456789입니다", "12-3456789"),
    (LicensePlateRecognizer, "LICENSE_PLATE", "لوحة السيارة34ABC123", "34ABC123"),
    (LicensePlateRecognizer, "LICENSE_PLATE", "plate no ทะเบียน34ABC123ครับ", "34ABC123"),
    (LicensePlateRecognizer, "LICENSE_PLATE", "plate no 번호34ABC123입니다", "34ABC123"),
]


@pytest.mark.parametrize(("recognizer", "entity", "text", "value"), _BASELINE_CASES)
def test_baseline_identifier_against_glued_script_has_its_exact_span(
    recognizer, entity: str, text: str, value: str
) -> None:
    assert _found([recognizer()], text, entity) == [value]


_PACK_CASES = [
    # (regulation, entity, text, value)
    ("kvkk", "NATIONAL_ID", "身份证10000000146号", "10000000146"),
    ("kvkk", "NATIONAL_ID", "ID번호: 12345678", "12345678"),
    ("kvkk", "TAX_ID", "VKNは1234567890です", "1234567890"),
    ("kvkk", "CUSTOMER_REFERENCE_ID", "顧客Müşteri No: ETP-2021-00489", "ETP-2021-00489"),
    ("lgpd", "TAX_ID", "注册号11.222.333/0001-81的", "11.222.333/0001-81"),
    ("lgpd", "NATIONAL_ID", "身份RG: 12.345.678-9", "12.345.678-9"),
    ("gdpr", "IP_ADDRESS", "地址192.168.1.1的", "192.168.1.1"),
    ("gdpr", "NATIONAL_ID", "外国人DNI: 12345678Z", "12345678Z"),
    ("gdpr", "NATIONAL_ID", "外国人NIE: X1234567L", "X1234567L"),
    ("gdpr", "SOCIAL_SECURITY_NUMBER", "番号NSS: 28 12345678 40", "28 12345678 40"),
    ("gdpr", "SOCIAL_SECURITY_NUMBER", "番号NIR: 1 85 05 78 006 084 36", "1 85 05 78 006 084 36"),
    ("gdpr", "TAX_ID", "公司SIREN: 834 291 076", "834 291 076"),
    ("hipaa", "MEDICAL_RECORD_NUMBER", "病历MRN-1234567号", "MRN-1234567"),
    ("hipaa", "HEALTH_INSURANCE_ID", "保险AB12345678号", "AB12345678"),
    ("uk_gdpr", "NATIONAL_ID", "番号AB123456C的", "AB123456C"),
    ("pipl", "NATIONAL_ID", "身份证11010519491231002X号", "11010519491231002X"),
    ("pdpl_sa", "NATIONAL_ID", "و1012345678", "1012345678"),
    ("nzpa", "TAX_ID", "番号49-091-850的", "49-091-850"),
    ("pdpa_th", "NATIONAL_ID", "เลขบัตร1-2345-67890-12-3ครับ", "1-2345-67890-12-3"),
    ("australia_pa", "TAX_ID", "番号123-456-782的", "123-456-782"),
    ("australia_pa", "HEALTH_INSURANCE_ID", "番号2123-45670-1的", "2123-45670-1"),
    ("pipeda", "NATIONAL_ID", "번호046-454-286입니다", "046-454-286"),
    ("popia", "NATIONAL_ID", "号码8001015009087的", "8001015009087"),
    ("ccpa", "DRIVERS_LICENSE", "驾照A1234567号", "A1234567"),
    ("cpra", "DRIVERS_LICENSE", "驾照A1234567号", "A1234567"),
    ("dpdp", "NATIONAL_ID", "番号2345 6789 0123的", "2345 6789 0123"),
    ("pdpa_sg", "NATIONAL_ID", "号码S1234567D的", "S1234567D"),
    ("appi", "NATIONAL_ID", "マイナンバー1234-5678-9012です", "1234-5678-9012"),
]


@pytest.mark.parametrize(("regulation", "entity", "text", "value"), _PACK_CASES)
def test_pack_identifier_against_glued_script_has_its_exact_span(
    regulation: str, entity: str, text: str, value: str
) -> None:
    assert _found(_pack(regulation), text, entity) == [value]


# ── Acceptance examples ─────────────────────────────────────────────────────


def test_a_tax_cue_inside_a_longer_word_does_not_make_a_tax_id() -> None:
    assert _found([TaxIDRecognizer()], "protein 123456789", "TAX_ID") == []


@pytest.mark.parametrize(
    "text",
    ["specific 12-3456789", "magnificent 12-3456789", "continental 12-3456789", "unified 12-3456789"],
)
def test_short_tax_cues_match_only_as_whole_tokens(text: str) -> None:
    assert _found([TaxIDRecognizer()], text, "TAX_ID") == []


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("EIN 12-3456789", "12-3456789"),
        ("TIN: 12-3456789", "12-3456789"),
        ("CIF: B-86432178", "B-86432178"),
        ("Steuernummer 27/431/07892", "27/431/07892"),
        ("USt-IdNr. DE298431076", "DE298431076"),
        ("税号DE298431076", "DE298431076"),
    ],
)
def test_tax_cues_still_match_as_labels(text: str, value: str) -> None:
    assert _found([TaxIDRecognizer()], text, "TAX_ID") == [value]


def test_passport_glued_to_han_is_a_passport_end_to_end() -> None:
    config = SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=False)
    engine = SeptumEngine(regulations=[], config=config, semantic_port=NullSemanticDetectionPort())
    text = "护照E12345678"
    result = engine._detector.sanitize(text, "zh", AnonymizationMap(document_id=0, language="zh"))
    spans = [(s.entity_type, text[s.start : s.end]) for s in result.detected_spans]
    assert spans == [("PASSPORT_NUMBER", "E12345678")]


def test_protein_is_not_a_tax_id_end_to_end() -> None:
    config = SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=False)
    engine = SeptumEngine(regulations=[], config=config, semantic_port=NullSemanticDetectionPort())
    text = "protein 123456789"
    result = engine._detector.sanitize(text, "en", AnonymizationMap(document_id=0, language="en"))
    assert all(s.entity_type != "TAX_ID" for s in result.detected_spans)


# ── Latin and same-script no-regression guards ─────────────────────────────


@pytest.mark.parametrize(
    ("recognizer", "entity", "text"),
    [
        (ExtendedPhoneRecognizer, "PHONE_NUMBER", "SKU ABC138-1234-5678XYZ"),
        (ValidatedIBANRecognizer, "IBAN", "ref XTR330006100519786457841326"),
        (CreditCardNumberRecognizer, "CREDIT_CARD_NUMBER", "lot A4111111111111111"),
        (MACAddressRecognizer, "MAC_ADDRESS", "hash x00:1A:2B:3C:4D:5Ez"),
        (PassportNumberRecognizer, "PASSPORT_NUMBER", "passport codeE12345678x"),
        (TaxIDRecognizer, "TAX_ID", "tax id ZZ12-3456789Q"),
        (SSNRecognizer, "SOCIAL_SECURITY_NUMBER", "SSN ref A123-45-6789"),
    ],
)
def test_digit_run_inside_a_latin_word_stays_undetected(recognizer, entity: str, text: str) -> None:
    assert _found([recognizer()], text, entity) == []


@pytest.mark.parametrize(
    ("regulation", "entity", "text"),
    [
        ("kvkk", "NATIONAL_ID", "SKU13812345678X"),
        ("pipl", "NATIONAL_ID", "batch A11010519491231002X9"),
        ("hipaa", "MEDICAL_RECORD_NUMBER", "XMRN-1234567"),
        ("gdpr", "IP_ADDRESS", "v1192.168.1.1a"),
        ("kvkk", "NATIONAL_ID", "invalid 12345678"),
        ("lgpd", "NATIONAL_ID", "cargo 12.345.678-9"),
        ("gdpr", "NATIONAL_ID", "condni 12345678Z"),
    ],
)
def test_pack_identifier_inside_a_latin_word_stays_undetected(
    regulation: str, entity: str, text: str
) -> None:
    assert _found(_pack(regulation), text, entity) == []


@pytest.mark.parametrize(
    ("regulation", "text"),
    [
        # A longer run of Arabic-Indic or Thai digits is one number: no
        # nine-digit window inside it is reported.
        ("pipeda", "١٢٣٤٥٦٧٨٩١٢٣"),
        ("pipeda", "๑๒๓๔๕๖๗๘๙๑๒๓"),
    ],
)
def test_native_digit_run_is_not_split(regulation: str, text: str) -> None:
    assert _found(_pack(regulation), text, "NATIONAL_ID") == []


@pytest.mark.parametrize(
    ("recognizer", "entity", "text", "value"),
    [
        (PassportNumberRecognizer, "PASSPORT_NUMBER", "Reisepassnummer: C12345678", "C12345678"),
        (LicensePlateRecognizer, "LICENSE_PLATE", "Aracın plakası: 34 ABC 123", "34 ABC 123"),
        (DriversLicenseRecognizer, "DRIVERS_LICENSE", "Führerscheinnummer B072RRE2I55", "B072RRE2I55"),
    ],
)
def test_word_cues_still_match_inside_compounds_and_before_suffixes(
    recognizer, entity: str, text: str, value: str
) -> None:
    assert value in _found([recognizer()], text, entity)


def test_iban_span_stops_before_the_space_that_follows_it() -> None:
    text = "account TR330006100519786457841326 ok"
    assert _found([ValidatedIBANRecognizer()], text, "IBAN") == ["TR330006100519786457841326"]


@pytest.mark.parametrize(
    ("text", "entity"),
    [
        ("Adobe 12/03/1990", "DATE"),
        ("DOB 12/03/1990", "DATE_OF_BIRTH"),
        ("出生日期1990年3月12日", "DATE_OF_BIRTH"),
    ],
)
def test_short_birth_cue_matches_only_as_a_whole_token(text: str, entity: str) -> None:
    results = CalendarDateRecognizer().analyze(text, ["DATE", "DATE_OF_BIRTH"])
    assert [r.entity_type for r in results] == [entity]


def test_address_abbreviation_counts_before_a_glued_glyph() -> None:
    cue = StructuralAddressRecognizer()._structural_cue
    assert len(cue.findall("NO号 APT号")) == 2
    assert cue.findall("NOTE APTLY") == []


# ── The boundary helper itself ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("电话123请", ["123"]),
        ("전화123입니다", ["123"]),
        ("و123", ["123"]),
        ("โทร123ครับ", ["123"]),
        ("abc123", []),
        ("123abc", []),
        ("é123", []),
        ("Ж123", []),
        ("_123", []),
        ("x 123 y", ["123"]),
        ("١٢123", []),
        ("123๑", []),
    ],
)
def test_token_bounded(text: str, expected: list[str]) -> None:
    assert re.findall(token_bounded(r"\d{3}"), text) == expected


@pytest.mark.parametrize("text", ["护照xE12345678", "护照E12345678x请"])
def test_a_latin_letter_glued_on_one_side_still_rejects(text: str) -> None:
    assert _found([PassportNumberRecognizer()], text, "PASSPORT_NUMBER") == []
