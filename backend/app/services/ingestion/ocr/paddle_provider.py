from __future__ import annotations

"""PaddleOCR-based OCR provider with layout analysis.

OCR inference runs in a **persistent subprocess** so that PaddlePaddle's
~1.5 GB memory footprint is isolated from the main FastAPI process.
The worker stays alive between calls with the engine cached, so only
the first OCR call pays the model-loading cost.
"""

import atexit
import logging
import multiprocessing as mp
import os
import re
from typing import Any, Dict, List, Tuple

import numpy as np  # type: ignore[import]

from .base import OcrProvider

logger = logging.getLogger(__name__)

_CURRENCY_SUFFIX = re.compile(r"(\d)[εε€¢º£¥₺#tbも](?=\s|$)", re.IGNORECASE)
_CURRENCY_STANDALONE = re.compile(r"[εε€¢º£¥₺も]")

# ---------------------------------------------------------------------------
# Subprocess worker — runs in a long-lived child process
# ---------------------------------------------------------------------------

_worker_engine: Any = None


def _paddle_ocr_predict(
    image_bytes: bytes,
    image_shape: Tuple[int, ...],
    image_dtype_str: str,
    model_name: str,
) -> List[Dict[str, Any]]:
    """Run PaddleOCR in the worker subprocess.

    The engine is created once and cached in the module-level
    ``_worker_engine`` variable for the lifetime of the worker process.
    """
    global _worker_engine

    if _worker_engine is None:
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        from paddleocr import PaddleOCR  # type: ignore[import]

        _worker_engine = PaddleOCR(text_recognition_model_name=model_name)

    image = np.frombuffer(image_bytes, dtype=np.dtype(image_dtype_str)).reshape(
        image_shape
    )
    raw = _worker_engine.predict(image)

    if not raw or not isinstance(raw, list):
        return []

    pages: List[Dict[str, Any]] = []
    for page in raw:
        pd: Dict[str, Any] = {
            "rec_texts": list(page.get("rec_texts", [])),
            "rec_scores": [float(s) for s in page.get("rec_scores", [])],
        }
        polys = page.get("dt_polys", [])
        if polys:
            pd["dt_polys"] = [np.array(p).tolist() for p in polys]
        pages.append(pd)
    return pages


# ---------------------------------------------------------------------------
# Persistent subprocess pool (module-level, shared across all providers)
# ---------------------------------------------------------------------------

_ocr_pool: mp.pool.Pool | None = None
_ocr_pool_lock = mp.Lock()


def _get_ocr_pool() -> mp.pool.Pool:
    """Return the long-lived OCR worker pool, creating it on first call."""
    global _ocr_pool
    if _ocr_pool is not None:
        return _ocr_pool
    with _ocr_pool_lock:
        if _ocr_pool is not None:
            return _ocr_pool
        ctx = mp.get_context("spawn")
        _ocr_pool = ctx.Pool(1)
        return _ocr_pool


def shutdown_ocr_pool() -> None:
    """Terminate the OCR subprocess pool and release ~1.5 GB of memory."""
    global _ocr_pool
    if _ocr_pool is not None:
        _ocr_pool.terminate()
        _ocr_pool.join()
        _ocr_pool = None


atexit.register(shutdown_ocr_pool)


# ---------------------------------------------------------------------------
# Provider class
# ---------------------------------------------------------------------------


class PaddleOcrProvider(OcrProvider):
    """OCR provider backed by PaddleOCR.

    Provides built-in layout analysis for structured documents
    like menus, invoices, and forms with accurate character
    recognition across multiple languages.

    PaddleOCR runs in a persistent subprocess so that PaddlePaddle's
    ~1.5 GB footprint never enters the main process. The engine is
    cached in the worker, so only the first call pays the loading cost.
    """

    _MODEL_NAME = "PP-OCRv5_server_rec"

    def __init__(self, **options: Any) -> None:
        self._options = dict(options)

    def run_ocr(
        self,
        image_array: np.ndarray,
        languages: List[str],
    ) -> Tuple[List[str], List[float]]:
        page_results = self._predict_in_subprocess(image_array)
        if not page_results:
            return [], []

        texts: List[str] = []
        confidences: List[float] = []

        for page_result in page_results:
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

    def _predict_in_subprocess(
        self, image_array: np.ndarray,
    ) -> List[Dict[str, Any]]:
        """Send image to the persistent OCR worker and collect results."""
        pool = _get_ocr_pool()
        try:
            return pool.apply(
                _paddle_ocr_predict,
                (
                    image_array.tobytes(),
                    image_array.shape,
                    str(image_array.dtype),
                    self._MODEL_NAME,
                ),
            )
        except Exception as exc:
            logger.warning(
                "PaddleOCR subprocess failed (image shape=%s): %s",
                image_array.shape,
                exc,
            )
            return []

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
        """Split entries into vertical columns only for true multi-column layouts."""
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
