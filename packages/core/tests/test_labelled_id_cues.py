"""Glued or abbreviated ID labels put the value that follows in context.

"kimlikNo", "TCKimlikNo", "IDNo", "RG nº", "R.G." and "rgNumero" mark the
digits after them as an ID number; the same letters inside an unrelated word
do not. Every span is exact.
"""

from __future__ import annotations

import importlib
import logging

import pytest

from septum_core import SeptumCoreConfig, SeptumEngine
from septum_core.anonymization_map import AnonymizationMap
from septum_core.ports import NullSemanticDetectionPort

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


def _pack_found(regulation: str, text: str, entity: str) -> list[str]:
    recognizers = importlib.import_module(
        f"septum_core.recognizers.{regulation}.recognizers"
    ).get_recognizers()
    return sorted(
        {
            text[r.start : r.end]
            for recognizer in recognizers
            for r in recognizer.analyze(text, [entity])
            if r.entity_type == entity
        }
    )


# ── Glued Turkish and English ID labels (kvkk) ───────────────────────────────

_KVKK_LABELS = [
    ("kimlikNo: 12345678", "12345678"),
    ("KimlikNo 123456789", "123456789"),
    ("TCKimlikNo: 1234567890", "1234567890"),
    ("TC Kimlik No 12345678", "12345678"),
    ("T.C. Kimlik Numarası: 123456789", "123456789"),
    ("IDNo 123456789", "123456789"),
    ("IdNo: 1234567890", "1234567890"),
    ("idNumber=12345678", "12345678"),
    ('{"tcKimlikNo": "12345678901"}', "12345678901"),
]


@pytest.mark.parametrize(("text", "value"), _KVKK_LABELS)
def test_glued_kvkk_label_gives_national_id_context(text: str, value: str) -> None:
    assert value in _pack_found("kvkk", text, "NATIONAL_ID")


@pytest.mark.parametrize(("text", "value"), _KVKK_LABELS)
def test_glued_kvkk_label_is_a_national_id_end_to_end(text: str, value: str) -> None:
    assert ("NATIONAL_ID", value) in _detected(("kvkk",), text)


@pytest.mark.parametrize("text", ["NOVEMBER 12345678", "PAID 12345678", "Kimlikli 12345678"])
def test_kvkk_label_inside_an_unrelated_word_gives_no_context(text: str) -> None:
    assert _pack_found("kvkk", text, "NATIONAL_ID") == []


# ── Brazilian RG with a number sign or a glued label (lgpd) ──────────────────

_RG_LABELS = [
    ("RG nº 12.345.678-9", "12.345.678-9"),
    ("RG nº: 123456789", "123456789"),
    ("RG n. 12.345.678-9", "12.345.678-9"),
    ("RG n° 12.345.678-9", "12.345.678-9"),
    ("RG número 12.345.678-9", "12.345.678-9"),
    ("RG Nº. 123456789", "123456789"),
    ("rg_number: 123456789", "123456789"),
    ("rgNumero 123456789", "123456789"),
    ("RG_NUMERO: 12.345.678-9", "12.345.678-9"),
    ("Registro Geral nº 12.345.678-9", "12.345.678-9"),
    ("RG: 12.345.678-9", "12.345.678-9"),
    ("R.G.: 12.345.678-9", "12.345.678-9"),
    ("RG n.º 12.345.678-9", "12.345.678-9"),
    ("RG nr 12345678", "12345678"),
    ("Carteira de Identidade nº 12.345.678-9", "12.345.678-9"),
    ("(RG 12.345.678)", "12.345.678"),
    ("rgNo: 123456789", "123456789"),
    ("RG núm. 12345678", "12345678"),
    ("RG: １２.３４５.６７８-９", "１２.３４５.６７８-９"),
]


@pytest.mark.parametrize(("text", "value"), _RG_LABELS)
def test_rg_label_with_number_sign_gives_context(text: str, value: str) -> None:
    assert _pack_found("lgpd", text, "NATIONAL_ID") == [value]


@pytest.mark.parametrize(("text", "value"), _RG_LABELS)
def test_rg_label_with_number_sign_end_to_end(text: str, value: str) -> None:
    assert ("NATIONAL_ID", value) in _detected(("lgpd",), text)


@pytest.mark.parametrize("text", ["ORGANIZATION nº 123456789", "cargo número 123456789", "orgNumero 123456789", "DR.G. 123456789"])
def test_rg_cue_inside_another_word_gives_no_context(text: str) -> None:
    assert _pack_found("lgpd", text, "NATIONAL_ID") == []
