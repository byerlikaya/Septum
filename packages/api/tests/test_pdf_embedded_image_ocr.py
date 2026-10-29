"""Characterization of PdfIngester._ocr_page_images (pins behaviour before a refactor).

Pages and documents are in-memory fakes; OCR is replaced by a recorder.
"""

from __future__ import annotations

import io
from typing import Any

import numpy as np
import pytest
from PIL import Image
from septum_api.services.ingestion import pdf_ingester as pdf_ingester_module
from septum_api.services.ingestion.pdf_ingester import PdfIngester

BIG = PdfIngester._MIN_IMAGE_DIMENSION
SMALL = BIG - 1


def _png(mode: str = "RGB") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, (BIG, BIG)).save(buffer, format="PNG")
    return buffer.getvalue()


class _Page:
    def __init__(self, images: list[tuple[int, int, int]]) -> None:
        self._images = [(xref, 0, width, height) for xref, width, height in images]

    def get_images(self, full: bool = True) -> list[tuple[Any, ...]]:
        return self._images


class _Doc:
    def __init__(self, data: dict[int, Any]) -> None:
        self._data = data

    def extract_image(self, xref: int) -> Any:
        return self._data.get(xref)


class _Ocr:
    """Recorder for ``run_ocr``: every call is kept; ``returns`` is what each call yields."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.returns: tuple[list[str], list[float]] = ([], [])


@pytest.fixture
def ocr(monkeypatch: pytest.MonkeyPatch) -> _Ocr:
    recorder = _Ocr()

    def _fake(provider, image_array, languages, **options):
        recorder.calls.append({"array": image_array, "languages": list(languages), "options": options})
        return recorder.returns

    monkeypatch.setattr(pdf_ingester_module, "run_ocr", _fake)
    return recorder


def test_small_images_are_skipped_before_extraction(ocr: _Ocr) -> None:
    doc = _Doc({1: {"image": _png()}})

    assert PdfIngester()._ocr_page_images(_Page([(1, SMALL, BIG)]), doc) == ([], [])
    assert ocr.calls == []


def test_small_height_alone_skips_the_image(ocr: _Ocr) -> None:
    doc = _Doc({1: {"image": _png()}})

    assert PdfIngester()._ocr_page_images(_Page([(1, BIG, SMALL)]), doc) == ([], [])
    assert ocr.calls == []


def test_image_without_extractable_data_is_skipped(ocr: _Ocr) -> None:
    assert PdfIngester()._ocr_page_images(_Page([(7, BIG, BIG)]), _Doc({})) == ([], [])
    assert ocr.calls == []


def test_recognised_text_is_joined_per_image_and_confidences_kept(ocr: _Ocr) -> None:
    ocr.returns = (["line one", "line two"], [0.9, 0.7])
    doc = _Doc({1: {"image": _png()}, 2: {"image": _png()}})

    texts, confidences = PdfIngester()._ocr_page_images(_Page([(1, BIG, BIG), (2, BIG, BIG)]), doc)

    assert texts == ["line one\nline two", "line one\nline two"]
    assert confidences == [0.9, 0.7, 0.9, 0.7]


def test_a_non_string_ocr_result_skips_that_image_without_failing_the_page(ocr: _Ocr) -> None:
    ocr.returns = (["fine", 5], [0.9])
    doc = _Doc({1: {"image": _png()}})

    assert PdfIngester()._ocr_page_images(_Page([(1, BIG, BIG)]), doc) == ([], [])


def test_empty_ocr_output_adds_neither_text_nor_confidence(ocr: _Ocr) -> None:
    ocr.returns = ([], [0.5])
    doc = _Doc({1: {"image": _png()}})

    assert PdfIngester()._ocr_page_images(_Page([(1, BIG, BIG)]), doc) == ([], [])
    assert len(ocr.calls) == 1


def test_non_rgb_images_are_converted_before_ocr(ocr: _Ocr) -> None:
    doc = _Doc({1: {"image": _png("L")}})

    PdfIngester()._ocr_page_images(_Page([(1, BIG, BIG)]), doc)

    array = ocr.calls[0]["array"]
    assert isinstance(array, np.ndarray)
    assert array.shape[-1] == 3


def test_english_is_appended_once_after_the_configured_languages(ocr: _Ocr) -> None:
    doc = _Doc({1: {"image": _png()}})

    PdfIngester(languages=["tr", "tr", "de"], ocr_provider_options={"lang": "x"})._ocr_page_images(
        _Page([(1, BIG, BIG)]), doc
    )

    assert ocr.calls[0]["languages"] == ["tr", "de", "en"]
    assert ocr.calls[0]["options"] == {"lang": "x"}


def test_english_is_the_default_when_no_languages_are_configured(ocr: _Ocr) -> None:
    doc = _Doc({1: {"image": _png()}})

    PdfIngester()._ocr_page_images(_Page([(1, BIG, BIG)]), doc)

    assert ocr.calls[0]["languages"] == ["en"]
