"""PDF pages with no text layer but something drawn on them are OCR'd whole.

A scanned page cut into small tiles, or text drawn as vector outlines,
has an empty text layer and no image large enough for the embedded-image
pass, so its content used to vanish silently. Truly blank pages are
still skipped, and pages with a text layer keep their behaviour.
"""

from __future__ import annotations

import io
from typing import Any

import fitz
import pytest
from PIL import Image

from septum_api.services.ingestion import pdf_ingester as pdf_ingester_module
from septum_api.services.ingestion.pdf_ingester import PdfIngester

_PAGE_OCR = "page text"
_IMAGE_OCR = "image text"


class _Ocr:
    def __init__(self) -> None:
        self.shapes: list[tuple[int, ...]] = []


@pytest.fixture
def ocr(monkeypatch: pytest.MonkeyPatch) -> _Ocr:
    recorder = _Ocr()

    def _fake(provider, image_array, languages, **options):
        recorder.shapes.append(image_array.shape)
        # A full-page render is 4x the page size; an embedded image keeps its own.
        text = _PAGE_OCR if image_array.shape[1] > 1000 else _IMAGE_OCR
        return [text], [0.9]

    monkeypatch.setattr(pdf_ingester_module, "run_ocr", _fake)
    return recorder


def _png(size: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (size, size), (200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def _extract(build) -> tuple[list[str], list[float]]:
    doc = fitz.open()
    page = doc.new_page()
    build(page)
    data = doc.tobytes()
    doc.close()
    with fitz.open(stream=data, filetype="pdf") as reopened:
        return PdfIngester()._extract_pages_with_optional_ocr(reopened)


def test_vector_drawn_page_is_ocred_whole(ocr: _Ocr) -> None:
    def build(page: Any) -> None:
        page.draw_rect(fitz.Rect(50, 50, 300, 120), color=(0, 0, 0), fill=(0, 0, 0))

    texts, confidences = _extract(build)
    assert texts == [_PAGE_OCR]
    assert confidences == [0.9]
    assert len(ocr.shapes) == 1


def test_page_of_small_tiles_is_ocred_whole(ocr: _Ocr) -> None:
    tile = _png(PdfIngester._MIN_IMAGE_DIMENSION - 50)

    def build(page: Any) -> None:
        for row in range(3):
            for col in range(3):
                x, y = 50 + col * 100, 50 + row * 100
                page.insert_image(fitz.Rect(x, y, x + 100, y + 100), stream=tile)

    texts, _ = _extract(build)
    assert texts == [_PAGE_OCR]
    assert len(ocr.shapes) == 1


def test_blank_page_is_not_ocred(ocr: _Ocr) -> None:
    texts, confidences = _extract(lambda page: None)
    assert texts == [""]
    assert confidences == []
    assert ocr.shapes == []


def test_page_with_a_text_layer_is_not_ocred_whole(ocr: _Ocr) -> None:
    def build(page: Any) -> None:
        page.insert_text((72, 72), "Invoice 2024")
        page.draw_rect(fitz.Rect(50, 100, 300, 120), color=(0, 0, 0))

    texts, _ = _extract(build)
    assert texts == ["Invoice 2024"]
    assert ocr.shapes == []


def test_textless_page_with_a_large_image_is_ocred_once(ocr: _Ocr) -> None:
    image = _png(PdfIngester._MIN_IMAGE_DIMENSION + 50)

    def build(page: Any) -> None:
        page.insert_image(fitz.Rect(50, 50, 400, 400), stream=image)

    texts, _ = _extract(build)
    assert texts == [_PAGE_OCR]
    assert len(ocr.shapes) == 1
