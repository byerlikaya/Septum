from __future__ import annotations

"""PaddleOCR-based OCR provider with layout analysis."""

import logging
import os
import re
from typing import Any, Dict, List, Tuple

import numpy as np  # type: ignore[import]

from .base import OcrProvider

logger = logging.getLogger(__name__)

_CURRENCY_SUFFIX = re.compile(r"(\d)[εε€¢º£¥₺#tbも](?=\s|$)", re.IGNORECASE)
_CURRENCY_STANDALONE = re.compile(r"[εε€¢º£¥₺も]")


class PaddleOcrProvider(OcrProvider):
    """OCR provider backed by PaddleOCR.

    Provides built-in layout analysis for structured documents
    like menus, invoices, and forms with accurate character
    recognition across multiple languages.
    """

    def __init__(self, **options: Any) -> None:
        self._options = dict(options)
        self._engine_cache: Dict[str, Any] = {}

    def run_ocr(
        self,
        image_array: np.ndarray,
        languages: List[str],
    ) -> Tuple[List[str], List[float]]:
        engine = self._get_engine(languages)
        if engine is None:
            return [], []

        try:
            result = engine.predict(image_array)
        except Exception as exc:
            logger.warning("PaddleOCR predict failed (image shape=%s): %s",
                           image_array.shape, exc)
            return [], []
        if not result or not isinstance(result, list):
            return [], []

        texts: List[str] = []
        confidences: List[float] = []

        for page_result in result:
            rec_texts = page_result.get("rec_texts", [])
            rec_scores = page_result.get("rec_scores", [])
            dt_polys = page_result.get("dt_polys", [])

            if dt_polys and len(dt_polys) == len(rec_texts):
                entries = self._build_entries(dt_polys, rec_texts, rec_scores)
                image_width = image_array.shape[1] if len(image_array.shape) >= 2 else 0
                columns = self._detect_columns(entries, image_width)

                for col_entries in columns:
                    lines = self._group_into_lines(col_entries)
                    for line_text, line_confs in lines:
                        cleaned = self._post_process(line_text)
                        if cleaned.strip():
                            texts.append(cleaned)
                            confidences.extend(line_confs)
                    if len(columns) > 1:
                        texts.append("")
                if texts and texts[-1] == "":
                    texts.pop()
            else:
                for text, score in zip(rec_texts, rec_scores):
                    cleaned = self._post_process(text)
                    if cleaned.strip():
                        texts.append(cleaned)
                        try:
                            confidences.append(float(score))
                        except (TypeError, ValueError):
                            confidences.append(0.0)

        return texts, confidences

    @staticmethod
    def _build_entries(
        polys: Any,
        rec_texts: List[str],
        rec_scores: List,
    ) -> List[Tuple[float, float, float, float, str, float]]:
        """Build (y_center, x_left, x_right, height, text, conf) entries."""
        entries: List[Tuple[float, float, float, float, str, float]] = []
        for i, poly in enumerate(polys):
            text = rec_texts[i] if i < len(rec_texts) else ""
            if not text or not text.strip():
                continue
            try:
                score = float(rec_scores[i]) if i < len(rec_scores) else 0.0
            except (TypeError, ValueError):
                score = 0.0
            points = np.array(poly)
            y_center = float(points[:, 1].mean())
            x_left = float(points[:, 0].min())
            x_right = float(points[:, 0].max())
            height = float(points[:, 1].max() - points[:, 1].min())
            entries.append((y_center, x_left, x_right, height, text.strip(), score))
        return entries

    @staticmethod
    def _detect_columns(
        entries: List[Tuple[float, float, float, float, str, float]],
        image_width: int,
    ) -> List[List[Tuple[float, float, float, float, str, float]]]:
        """Split entries into vertical columns only for true multi-column layouts.

        Distinguishes between:
        - True multi-column: both sides have text content (menus with 2 product lists)
        - Price-right layout: left has text, right has only short numbers/prices
          (single-column menu with prices aligned right)
        """
        if not entries or image_width < 400:
            return [entries]

        mid_x = image_width / 2

        left = []
        right = []
        for e in entries:
            x_center = (e[1] + e[2]) / 2
            width = e[2] - e[1]
            if width > image_width * 0.5:
                left.append(e)
                continue
            if x_center < mid_x:
                left.append(e)
            else:
                right.append(e)

        if not right or len(right) < 3:
            return [entries]

        right_has_text = sum(
            1 for e in right
            if len(e[4]) > 6 and not re.match(r"^[\d.,₺€$£#tb\s+\-]+$", e[4], re.IGNORECASE)
        )
        right_total = len(right)

        if right_has_text < right_total * 0.3:
            return [entries]

        return [left, right]

    @staticmethod
    def _group_into_lines(
        entries: List[Tuple[float, float, float, float, str, float]],
    ) -> List[Tuple[str, List[float]]]:
        """Group entries into reading-order lines by Y proximity."""
        if not entries:
            return []

        entries = sorted(entries, key=lambda e: (e[0], e[1]))

        heights = [e[3] for e in entries if e[3] > 0]
        if heights:
            heights.sort()
            median_h = heights[len(heights) // 2]
        else:
            median_h = 20.0
        threshold = median_h * 0.5

        lines: List[List[Tuple[float, str, float]]] = []
        current: List[Tuple[float, str, float]] = []
        current_y = entries[0][0]

        for y_c, x_l, _x_r, _h, text, conf in entries:
            if abs(y_c - current_y) > threshold and current:
                lines.append(current)
                current = []
                current_y = y_c
            current.append((x_l, text, conf))
            n = len(current)
            current_y = current_y + (y_c - current_y) / n

        if current:
            lines.append(current)

        result: List[Tuple[str, List[float]]] = []
        for line in lines:
            sorted_line = sorted(line, key=lambda item: item[0])
            line_text = "  ".join(item[1] for item in sorted_line)
            confs = [item[2] for item in sorted_line]
            result.append((line_text, confs))
        return result

    @staticmethod
    def _post_process(text: str) -> str:
        """Fix common OCR misrecognitions."""
        text = _CURRENCY_SUFFIX.sub(r"\1₺", text)
        text = _CURRENCY_STANDALONE.sub("₺", text)
        return text

    def _get_engine(self, languages: List[str]) -> Any:
        """Get or create a PaddleOCR engine.

        Uses the multilingual server recognition model (PP-OCRv5_server_rec)
        which supports all scripts without language-specific configuration.
        """
        cache_key = "multilingual"
        if cache_key in self._engine_cache:
            return self._engine_cache[cache_key]

        try:
            from paddleocr import PaddleOCR  # type: ignore[import]
        except ImportError:
            logger.error("paddleocr package not installed")
            return None

        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

        try:
            engine = PaddleOCR(
                text_recognition_model_name="PP-OCRv5_server_rec",
            )
            self._engine_cache[cache_key] = engine
            return engine
        except Exception as exc:
            logger.error("Failed to initialize PaddleOCR: %s", exc)
            return None
