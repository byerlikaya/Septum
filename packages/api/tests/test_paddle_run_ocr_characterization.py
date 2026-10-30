"""Characterization tests for :meth:`PaddleOcrProvider.run_ocr`.

The OCR engine is replaced by canned page results (no subprocess, no
model download) so the exact text lines, column separators and
confidence lists ``run_ocr`` assembles from them are pinned for both
the layout-aware path (detection polygons present) and the plain path.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from septum_api.services.ingestion.ocr.paddle_provider import PaddleOcrProvider


def _box(x1: float, y1: float, x2: float, y2: float) -> list[list[float]]:
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def _provider(monkeypatch, pages: Any) -> PaddleOcrProvider:
    provider = PaddleOcrProvider()
    seen: list[Any] = []

    def fake_predict(image_array):
        seen.append(image_array)
        return pages

    monkeypatch.setattr(provider, "_predict_in_subprocess", fake_predict)
    setattr(provider, "seen", seen)
    return provider


def _image(width: int, height: int = 200) -> np.ndarray:
    return np.zeros((height, width, 3), dtype=np.uint8)


@pytest.mark.parametrize("pages", [[], None])
def test_no_results(monkeypatch, pages):
    provider = _provider(monkeypatch, pages)
    image = _image(300)
    assert provider.run_ocr(image, ["en"]) == ([], [])
    assert provider.seen == [image]


def test_plain_path_without_polygons(monkeypatch):
    pages = [
        {
            "rec_texts": ["Hello", "12€", "   ", "Price 5 ε", "bad", "str", "extra"],
            "rec_scores": [0.9, 0.8, 0.7, 0.6, None, "0.5"],
        }
    ]
    provider = _provider(monkeypatch, pages)
    assert provider.run_ocr(_image(300), ["en"]) == (
        ["Hello", "12₺", "Price 5 ₺", "bad", "str"],
        [0.9, 0.8, 0.6, 0.0, 0.5],
    )


def test_plain_path_bad_score_string(monkeypatch):
    pages = [{"rec_texts": ["A"], "rec_scores": ["x"]}]
    assert _provider(monkeypatch, pages).run_ocr(_image(300), ["en"]) == (["A"], [0.0])


def test_polygon_count_mismatch_uses_plain_path(monkeypatch):
    pages = [
        {
            "rec_texts": ["World", "Hello"],
            "rec_scores": [0.9, 0.8],
            "dt_polys": [_box(100, 10, 150, 30)],
        }
    ]
    assert _provider(monkeypatch, pages).run_ocr(_image(300), ["en"]) == (
        ["World", "Hello"],
        [0.9, 0.8],
    )


def test_missing_keys_yield_nothing(monkeypatch):
    assert _provider(monkeypatch, [{}]).run_ocr(_image(300), ["en"]) == ([], [])


def test_layout_single_column_groups_lines(monkeypatch):
    pages = [
        {
            "rec_texts": ["World", "Hello", "12€", "   ", " Total ", "x"],
            "rec_scores": [0.9, 0.8, 0.7, 0.6, "bad", 0.4],
            "dt_polys": [
                _box(100, 10, 150, 30),
                _box(10, 12, 60, 30),
                _box(10, 50, 50, 70),
                _box(10, 70, 60, 90),
                _box(10, 90, 60, 110),
                _box(200, 92, 220, 110),
            ],
        }
    ]
    assert _provider(monkeypatch, pages).run_ocr(_image(300), ["en"]) == (
        ["Hello  World", "12₺", "Total  x"],
        [0.8, 0.9, 0.7, 0.0, 0.4],
    )


def test_layout_two_columns_are_separated(monkeypatch):
    pages = [
        {
            "rec_texts": ["Left one", "Left two", "Right alpha", "Right beta", "Right gamma", "Wide banner"],
            "rec_scores": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
            "dt_polys": [
                _box(10, 10, 200, 30),
                _box(10, 50, 200, 70),
                _box(450, 10, 700, 30),
                _box(450, 50, 700, 70),
                _box(450, 90, 700, 110),
                _box(10, 130, 790, 150),
            ],
        }
    ]
    assert _provider(monkeypatch, pages).run_ocr(_image(800), ["en"]) == (
        ["Left one", "Left two", "Wide banner", "", "Right alpha", "Right beta", "Right gamma"],
        [0.1, 0.2, 0.6, 0.3, 0.4, 0.5],
    )


def test_layout_numeric_right_side_stays_single_column(monkeypatch):
    pages = [
        {
            "rec_texts": ["Coffee latte", "12.50", "Tea", "9.00", "Cake", "7.25"],
            "rec_scores": [0.9] * 6,
            "dt_polys": [
                _box(10, 10, 200, 30),
                _box(600, 10, 700, 30),
                _box(10, 50, 200, 70),
                _box(600, 50, 700, 70),
                _box(10, 90, 200, 110),
                _box(600, 90, 700, 110),
            ],
        }
    ]
    assert _provider(monkeypatch, pages).run_ocr(_image(800), ["en"]) == (
        ["Coffee latte  12.50", "Tea  9.00", "Cake  7.25"],
        [0.9] * 6,
    )


def test_layout_only_right_column_keeps_leading_separator(monkeypatch):
    pages = [
        {
            "rec_texts": ["Right alpha", "Right beta", "Right gamma"],
            "rec_scores": [0.3, 0.4, 0.5],
            "dt_polys": [
                _box(450, 10, 700, 30),
                _box(450, 50, 700, 70),
                _box(450, 90, 700, 110),
            ],
        }
    ]
    assert _provider(monkeypatch, pages).run_ocr(_image(800), ["en"]) == (
        ["", "Right alpha", "Right beta", "Right gamma"],
        [0.3, 0.4, 0.5],
    )


def test_layout_empty_columns_leave_one_separator(monkeypatch):
    provider = _provider(monkeypatch, [{"rec_texts": ["a"], "rec_scores": [1.0], "dt_polys": [_box(0, 0, 1, 1)]}])
    monkeypatch.setattr(PaddleOcrProvider, "_detect_columns", staticmethod(lambda entries, width: [[], []]))
    assert provider.run_ocr(_image(800), ["en"]) == ([""], [])


def test_multi_page_mixes_paths(monkeypatch):
    pages = [
        {
            "rec_texts": ["Left one", "Right alpha", "Right beta", "Right gamma"],
            "rec_scores": [0.1, 0.3, 0.4, 0.5],
            "dt_polys": [
                _box(10, 10, 200, 30),
                _box(450, 10, 700, 30),
                _box(450, 50, 700, 70),
                _box(450, 90, 700, 110),
            ],
        },
        {"rec_texts": ["Page two"], "rec_scores": [0.7]},
        {"rec_texts": ["Third"], "rec_scores": [0.8], "dt_polys": [_box(10, 10, 60, 30)]},
    ]
    assert _provider(monkeypatch, pages).run_ocr(_image(800), ["en"]) == (
        ["Left one", "", "Right alpha", "Right beta", "Right gamma", "Page two", "Third"],
        [0.1, 0.3, 0.4, 0.5, 0.7, 0.8],
    )


def test_one_dimensional_image_has_no_width(monkeypatch):
    pages = [
        {
            "rec_texts": ["Left one", "Right alpha", "Right beta", "Right gamma"],
            "rec_scores": [0.1, 0.3, 0.4, 0.5],
            "dt_polys": [
                _box(10, 10, 200, 30),
                _box(450, 50, 700, 70),
                _box(450, 90, 700, 110),
                _box(450, 130, 700, 150),
            ],
        }
    ]
    provider = _provider(monkeypatch, pages)
    assert provider.run_ocr(np.zeros(5, dtype=np.uint8), ["en"]) == (
        ["Left one", "Right alpha", "Right beta", "Right gamma"],
        [0.1, 0.3, 0.4, 0.5],
    )
