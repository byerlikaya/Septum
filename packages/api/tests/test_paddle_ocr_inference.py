"""Regression test: PaddleOCR must run real inference through Septum's provider.

paddlepaddle 3.3.x raises inside ``predict()`` on linux/amd64 (oneDNN PIR
regression, Paddle issue #77340), while every mocked test still passes. This
test runs the PP-OCRv5 models on a synthetic image. The first run downloads the
models into the PaddleX cache (``~/.paddlex``), which CI caches.
"""

from __future__ import annotations

from typing import Iterator

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont
from septum_api.services.ingestion.ocr.paddle_provider import (
    PaddleOcrProvider,
    shutdown_ocr_pool,
)

EXPECTED_TEXT = "Hello 12345"


@pytest.fixture(scope="module")
def synthetic_text_image() -> np.ndarray:
    font = ImageFont.load_default(size=48)
    canvas = Image.new("RGB", (480, 120), color="white")
    ImageDraw.Draw(canvas).text((20, 30), EXPECTED_TEXT, fill="black", font=font)
    return np.asarray(canvas)


@pytest.fixture(scope="module")
def release_ocr_worker() -> Iterator[None]:
    yield
    shutdown_ocr_pool()


@pytest.mark.usefixtures("release_ocr_worker")
def test_paddleocr_recognises_synthetic_text(synthetic_text_image: np.ndarray) -> None:
    texts, confidences = PaddleOcrProvider().run_ocr(synthetic_text_image, ["en"])

    assert texts, (
        "PaddleOCR returned no text. The worker most likely failed; "
        "look for the 'PaddleOCR subprocess failed' warning."
    )
    recognised = " ".join(texts)
    assert "Hello" in recognised, recognised
    assert "12345" in recognised, recognised
    assert confidences and min(confidences) > 0.5, confidences
