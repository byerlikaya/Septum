from __future__ import annotations

"""Abstract base for OCR providers."""

from abc import ABC, abstractmethod
from typing import List, Tuple

import numpy as np  # type: ignore[import]


class OcrProvider(ABC):
    """Interface for OCR engines used during document and image ingestion."""

    @abstractmethod
    def run_ocr(
        self,
        image_array: np.ndarray,
        languages: List[str],
    ) -> Tuple[List[str], List[float]]:
        """Extract text and per-fragment confidence from an image array.

        Args:
            image_array: Grayscale or RGB numpy array (H, W) or (H, W, C).
            languages: List of language codes from application settings (e.g. en, tr).

        Returns:
            Tuple of (list of text fragments in reading order, list of confidence scores).
        """
        ...
