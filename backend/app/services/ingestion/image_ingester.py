from __future__ import annotations

"""Image document ingester using EasyOCR.

This ingester is responsible for:
    - Reading encrypted image bytes from disk.
    - Decrypting them in memory using the shared AES-256-GCM utilities.
    - Running OCR via EasyOCR to extract plain text.
    - Returning an :class:`IngestionResult` with the concatenated text and
      lightweight, non-PII metadata (e.g., image size, average confidence).

All heavy OCR work is executed in a worker thread to keep the async event loop
responsive. No raw PII is ever logged; extracted text only flows through the
sanitization pipeline and is not persisted in plaintext on disk.
"""

import asyncio
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np  # type: ignore[import]
from PIL import Image  # type: ignore[import]

from ...utils.crypto import decrypt
from ...utils.device import get_device
from .base import BaseIngester, IngestionResult


class ImageIngester(BaseIngester):
    """Ingests encrypted image files and extracts textual content via OCR."""

    def __init__(self, languages: List[str] | None = None) -> None:
        """Initialize the ingester with optional OCR language hints.

        Args:
            languages: Optional list of EasyOCR language codes (e.g., ``["en"]``).
                If omitted, English-only OCR is used by default. Language
                selection is intentionally narrow by default to reduce false
                positives; callers can provide a broader set when needed.
        """

        self._languages: List[str] = languages or ["en"]

    async def extract(self, data: bytes, filename: str) -> IngestionResult:
        """Extract text from raw (unencrypted) image bytes.

        This helper mirrors :meth:`PdfIngester.extract` and exists primarily
        for ad-hoc, local usage such as:

            with open("sample.jpg", "rb") as f:
                result = await ImageIngester().extract(f.read(), "sample.jpg")

        It does not perform any encryption/decryption and MUST NOT be used
        for files managed by the main ingestion pipeline, where all files are
        stored encrypted on disk.
        """

        return await asyncio.to_thread(self._extract_sync, data, filename)

    def _extract_sync(self, data: bytes, filename: str) -> IngestionResult:
        """Synchronous helper for :meth:`extract`, run in a worker thread."""

        with Image.open(BytesIO(data)) as img:
            width, height = img.size

        texts, confidences = self._run_ocr(data)

        full_text = "\n".join(texts)
        avg_confidence = (
            float(sum(confidences) / len(confidences)) if confidences else 0.0
        )

        warnings: List[str] = []
        if not texts:
            warnings.append("No text detected by OCR.")

        metadata: Dict[str, Any] = {
            "filename": filename,
            "image_width": width,
            "image_height": height,
            "ocr_avg_confidence": avg_confidence,
        }

        return IngestionResult(
            text=full_text,
            metadata=metadata,
            confidence=avg_confidence,
            warnings=warnings,
        )

    async def ingest(
        self,
        file_path: Path,
        *,
        mime_type: str,
        file_format: str,
    ) -> IngestionResult:
        """Ingest the encrypted image at ``file_path`` and return extracted text."""

        return await asyncio.to_thread(
            self._ingest_sync,
            file_path,
            mime_type,
            file_format,
        )

    def _ingest_sync(
        self,
        file_path: Path,
        mime_type: str,
        file_format: str,
    ) -> IngestionResult:
        """Synchronous part of image ingestion, run in a worker thread."""

        encrypted_bytes = file_path.read_bytes()
        image_bytes = decrypt(encrypted_bytes)

        # Open the image from decrypted bytes to capture basic, non-PII metadata
        # such as dimensions without ever touching disk in plaintext.
        with Image.open(BytesIO(image_bytes)) as img:
            width, height = img.size

        texts, confidences = self._run_ocr(image_bytes)

        full_text = "\n".join(texts)
        avg_confidence = (
            float(sum(confidences) / len(confidences)) if confidences else 0.0
        )

        metadata: Dict[str, Any] = {
            "mime_type": mime_type,
            "file_format": file_format,
            "image_width": width,
            "image_height": height,
            "ocr_avg_confidence": avg_confidence,
        }

        return IngestionResult(text=full_text, metadata=metadata)

    def _run_ocr(self, image_bytes: bytes) -> Tuple[List[str], List[float]]:
        """Run EasyOCR on the given image bytes and return text + confidences."""

        # Import locally so that environments without EasyOCR installed
        # can still import the module without immediate failure.
        import easyocr  # type: ignore[import]

        device = get_device()
        # EasyOCR currently exposes a simple GPU toggle; treat CUDA as GPU and
        # fall back to CPU for MPS and other environments.
        use_gpu = device == "cuda"

        reader = easyocr.Reader(self._languages, gpu=use_gpu)

        # Convert decrypted bytes to a NumPy array via Pillow so that the
        # plaintext image data never needs to be written back to disk.
        with Image.open(BytesIO(image_bytes)) as img:
            image_array = np.array(img.convert("RGB"))

        results = reader.readtext(image_array, detail=1)

        texts: List[str] = []
        confidences: List[float] = []

        for _bbox, text, confidence in results:
            if not text:
                continue
            texts.append(text)
            # Confidence values are expected to be in [0.0, 1.0]; cast defensively.
            try:
                confidences.append(float(confidence))
            except (TypeError, ValueError):
                continue

        return texts, confidences

