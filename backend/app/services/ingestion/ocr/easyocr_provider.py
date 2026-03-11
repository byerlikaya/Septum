from __future__ import annotations

"""EasyOCR-based OCR provider."""

import logging
from typing import Any, Dict, List, Tuple

import numpy as np  # type: ignore[import]

from ....utils.device import get_device
from .base import OcrProvider

logger = logging.getLogger(__name__)


class EasyOcrProvider(OcrProvider):
    """OCR provider backed by EasyOCR."""

    def __init__(self, **options: Any) -> None:
        self._options = dict(options)
        self._reader_cache: Dict[tuple, Any] = {}

    def run_ocr(
        self,
        image_array: np.ndarray,
        languages: List[str],
    ) -> Tuple[List[str], List[float]]:
        reader = self._get_reader(languages)
        if reader is None:
            return [], []

        results = reader.readtext(image_array, detail=1)
        texts: List[str] = []
        confidences: List[float] = []
        for _bbox, text, confidence in results:
            if not text:
                continue
            texts.append(text)
            try:
                confidences.append(float(confidence))
            except (TypeError, ValueError):
                continue
        return texts, confidences

    def _get_reader(self, languages: List[str]) -> Any:
        base = list(dict.fromkeys(languages or []))
        if "en" not in base:
            base.append("en")
        effective = base or ["en"]
        cache_key = tuple(effective)

        if cache_key in self._reader_cache:
            return self._reader_cache[cache_key]

        import easyocr  # type: ignore[import]

        device = get_device()
        use_gpu = device == "cuda"

        try:
            reader = easyocr.Reader(list(effective), gpu=use_gpu)
            self._reader_cache[cache_key] = reader
            return reader
        except ValueError:
            primary = [lang for lang in effective if lang != "en"]
            if "en" in effective:
                primary.append("en")
            for lang in primary:
                try:
                    candidate = ["en"] if lang == "en" else [lang, "en"]
                    reader = easyocr.Reader(candidate, gpu=use_gpu)
                    self._reader_cache[cache_key] = reader
                    return reader
                except ValueError:
                    continue
            reader = easyocr.Reader(["en"], gpu=use_gpu)
            self._reader_cache[cache_key] = reader
            return reader
