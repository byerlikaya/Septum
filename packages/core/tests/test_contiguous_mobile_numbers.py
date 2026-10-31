"""Mobile numbers written as one digit run are masked in every script.

A mobile number written without ``+`` or separators ("13812345678",
"电话13812345678请回电", "05321234567") is a phone number on its own when its
national shape is distinctive; a shape that many other numbers share
("5321234567", "9876543210") needs a phone cue next to it. Every span is exact.
"""

from __future__ import annotations

import logging

import pytest

from septum_core import SeptumCoreConfig, SeptumEngine
from septum_core.anonymization_map import AnonymizationMap
from septum_core.detector import ExtendedPhoneRecognizer
from septum_core.ports import NullSemanticDetectionPort

_CONFIG = SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=False)
_ENGINES: dict[tuple[str, ...], SeptumEngine] = {}

# Checksum-valid identifiers whose digits also have the shape of a mobile number.
_VALID_TCKN = "13812345658"
_VALID_CPF = "13912345619"


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


def _phones(text: str) -> list[str]:
    return sorted(text[r.start : r.end] for r in ExtendedPhoneRecognizer().analyze(text, ["PHONE_NUMBER"]))


# ── Contiguous mobile numbers ────────────────────────────────────────────────

_MOBILE_E2E = [
    (("pipl",), "13812345678", "13812345678"),
    (("pipl",), "电话13812345678请回电", "13812345678"),
    (("pipl",), "手机：13912345678。", "13912345678"),
    (("pipl",), "联系人张先生 15012345678", "15012345678"),
    (("pipl",), "05321234567", "05321234567"),
    (("appi",), "携帯09012345678です", "09012345678"),
    (("pipl",), "휴대폰01012345678입니다", "01012345678"),
    (("uk_gdpr",), "Call 07123456789 after 5pm", "07123456789"),
    (("gdpr",), "Handy 015112345678", "015112345678"),
    (("gdpr",), "Portable : 0612345678", "0612345678"),
    (("pdpa_th",), "มือถือ0812345678ครับ", "0812345678"),
    (("pdpl_sa",), "جوال0512345678", "0512345678"),
    (("pipl",), "Telefon: 5321234567", "5321234567"),
    (("pipl",), "Mobile no. 9876543210", "9876543210"),
    (("pipl",), "Celular 11912345678", "11912345678"),
    (("pipl",), "Phone 4155552671", "4155552671"),
]


@pytest.mark.parametrize(("regulations", "text", "value"), _MOBILE_E2E)
def test_contiguous_mobile_number_is_masked_end_to_end(
    regulations: tuple[str, ...], text: str, value: str
) -> None:
    assert _detected(regulations, text) == [("PHONE_NUMBER", value)]


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("13812345678", "13812345678"),
        ("电话13812345678请回电", "13812345678"),
        ("05321234567", "05321234567"),
        ("携帯09012345678です", "09012345678"),
        ("휴대폰01012345678입니다", "01012345678"),
        ("الهاتف0512345678", "0512345678"),
        ("SMS: 89161234567", "89161234567"),
        ("cep tel: 5321234567", "5321234567"),
        ("phoneNumber=9876543210", "9876543210"),
        ("Mob 0412345678", "0412345678"),
        ("NZ 0212345678", "0212345678"),
        ("0612345678", "0612345678"),
        ("08012345678", "08012345678"),
        ("017612345678", "017612345678"),
        ("Teléfono 612345678", "612345678"),
        ("Cellulare 3123456789", "3123456789"),
        ("Phone 91234567", "91234567"),
        ("هاتف 512345678", "512345678"),
        ("Telefone 11987654321", "11987654321"),
        ("Phone 2125552671", "2125552671"),
        ("Тел. 9876543210", "9876543210"),
        ("手機9876543210", "9876543210"),
        ("전화 9876543210", "9876543210"),
        ("मोबाइल 9876543210", "9876543210"),
        ("โทร 9876543210", "9876543210"),
        ("Phone: " + "x" * 30 + " 9876543210", "9876543210"),
        ("9876543210 (mobile)", "9876543210"),
        ("Fone: 11987654321", "11987654321"),
        ("WhatsApp 11987654321", "11987654321"),
        ("Contact No: 09876543210", "09876543210"),
        ("Ph: 9876543210", "9876543210"),
        ("HP: 91234567", "91234567"),
        ("Tlf: 612345678", "612345678"),
        ("Gsm 0470123456", "0470123456"),
        ("Telemóvel 912345678", "912345678"),
        ("Tel. 512345678", "512345678"),
        ("0712345678", "0712345678"),
        ("手机：１３８１２３４５６７８", "１３８１２３４５６７８"),
        ("1381234\u200b5678", "1381234\u200b5678"),
        ("Mobile: ９８７６５４３２１０", "９８７６５４３２１０"),
    ],
)
def test_phone_recognizer_finds_contiguous_mobile_number(text: str, value: str) -> None:
    assert _phones(text) == [value]


@pytest.mark.parametrize(
    "text",
    [
        # Shapes many other numbers share need a phone cue.
        "Order 5321234567 shipped",
        "Ref 9876543210",
        "Invoice 4155552671",
        "Phone " + "x" * 60 + " 9876543210",
        "Order: 9876543210 telegram",
        "PHP 9876543210",
        "Order 9876543210 shipped to " + "x" * 30 + " phone",
        "Hotel 9876543210",
        "Cellar 9876543210",
        "Phone 2121552671",
        "Sipariş 11912345678",
        # A mobile shape inside a longer number, word or decimal is no phone number.
        "138123456789012",
        "ABC13812345678",
        "13812345678XYZ",
        "Total 1.13812345678 BTC",
        "0,07123456789 kg",
        # Dates, timestamps and amounts.
        "20231015",
        "1698710400",
        "1500000000",
        "Amount 1698710400123",
    ],
)
def test_ambiguous_or_embedded_digit_runs_are_no_mobile_number(text: str) -> None:
    assert _phones(text) == []


def test_many_contiguous_mobiles_stay_linear() -> None:
    text = "05321234567 ve 532 123 45 67 ve " * 4000
    assert len(_phones(text)) == 8000


def test_international_mobile_stays_one_span_end_to_end() -> None:
    for text, value in (("+86 13812345678", "+86 13812345678"), ("+8613812345678", "+8613812345678")):
        assert _detected(("pipl",), text) == [("PHONE_NUMBER", value)]


def test_checksum_valid_national_id_keeps_its_label() -> None:
    assert _detected(("kvkk",), f"No {_VALID_TCKN}") == [("NATIONAL_ID", _VALID_TCKN)]


def test_checksum_valid_cpf_keeps_its_label() -> None:
    assert _detected(("lgpd",), f"Cliente {_VALID_CPF}") == [("CPF", _VALID_CPF)]


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("电话138-1234-5678请回电", "138-1234-5678"),
        ("Tel: 0532 123 45 67", "0532 123 45 67"),
        ("+90 532 123 45 67", "+90 532 123 45 67"),
    ],
)
def test_separated_phone_numbers_keep_their_spans(text: str, value: str) -> None:
    assert _phones(text) == [value]
