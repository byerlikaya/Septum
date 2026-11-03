"""OCR currency fixes stay inside the languages and contexts they belong to.

PaddleOCR misreads the Turkish lira sign ``₺`` as ``ε``, ``º``, ``#``,
``t``, ``b`` or ``も``. The fix runs only when a configured OCR language
lists such misreads, and only right after an amount. Real currency signs
(``€ £ ¥ ¢``) and the same glyphs elsewhere in the text, such as Greek
``ε`` or Japanese ``も``, are never rewritten.
"""

from __future__ import annotations

import pytest

from septum_api.services.ingestion.ocr.paddle_provider import PaddleOcrProvider

_fix = PaddleOcrProvider._post_process


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Toplam 1.250,00 ε", "Toplam 1.250,00 ₺"),
        ("Tutar: 500t", "Tutar: 500₺"),
        ("Fiyat 75b KDV dahil", "Fiyat 75₺ KDV dahil"),
        ("Ücret 40#", "Ücret 40₺"),
        ("Bakiye 12º", "Bakiye 12₺"),
        ("Bedel 9も", "Bedel 9₺"),
        ("Tutar 300₺", "Tutar 300₺"),
    ],
)
def test_turkish_hint_fixes_misread_lira_after_an_amount(text: str, expected: str) -> None:
    assert _fix(text, ["tr", "en"]) == expected


@pytest.mark.parametrize("languages", [["tr", "en"], ["en"], ["de"], ["el"], ["ja"]])
@pytest.mark.parametrize("text", ["Price 500 €", "£20 fee", "¥300", "Total 12€", "5¢ each"])
def test_real_currency_signs_are_never_rewritten(text: str, languages: list[str]) -> None:
    assert _fix(text, languages) == text


@pytest.mark.parametrize(
    ("text", "languages"),
    [
        ("Η τιμή είναι 5 ευρώ", ["el", "en"]),
        ("εταιρεία ε", ["tr", "en"]),
        ("これもそれも", ["ja", "en"]),
        ("1º lugar", ["es", "en"]),
        ("Room 12b, floor 3t", ["en"]),
    ],
)
def test_glyphs_outside_a_turkish_amount_are_kept(text: str, languages: list[str]) -> None:
    assert _fix(text, languages) == text


def test_without_a_turkic_hint_nothing_changes() -> None:
    assert _fix("Total 1,250.00 ε and 500t", ["en", "de"]) == "Total 1,250.00 ε and 500t"


def test_letters_inside_words_stay() -> None:
    assert _fix("Ankara 2024 tablo 3tb", ["tr"]) == "Ankara 2024 tablo 3tb"
