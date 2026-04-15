from __future__ import annotations

"""Pluggable OCR provider layer for document and image ingestion.

Default provider is PaddleOCR; language lists are passed from settings.
"""

from typing import Any, Dict, List, Tuple

import numpy as np  # type: ignore[import]

from .base import OcrProvider
from .paddle_provider import PaddleOcrProvider

_PROVIDERS: Dict[str, type[OcrProvider]] = {
    "paddleocr": PaddleOcrProvider,
}

DEFAULT_PROVIDER = "paddleocr"


def get_ocr_provider(name: str, **options: Any) -> OcrProvider:
    """Return an OCR provider instance for the given name and options."""
    normalized = (name or "").strip().lower() or DEFAULT_PROVIDER
    if normalized not in _PROVIDERS:
        normalized = DEFAULT_PROVIDER
    return _PROVIDERS[normalized](**options)


def run_ocr(
    provider_name: str,
    image_array: np.ndarray,
    languages: List[str],
    **provider_options: Any,
) -> Tuple[List[str], List[float]]:
    """Run OCR on a single image array using the configured provider."""
    provider = get_ocr_provider(provider_name, **provider_options)
    return provider.run_ocr(image_array, languages)
