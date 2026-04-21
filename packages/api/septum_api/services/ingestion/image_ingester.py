from __future__ import annotations

"""Image document ingester using the pluggable OCR provider layer.

This ingester is responsible for:
    - Reading encrypted image bytes from disk.
    - Decrypting them in memory using the shared AES-256-GCM utilities.
    - Running OCR via the shared OCR provider layer.
    - Returning an :class:`IngestionResult` with the concatenated text and
      lightweight, non-PII metadata (e.g., image size, average confidence).

All heavy OCR work is executed in a worker thread to keep the async event loop
responsive. No raw PII is ever logged; extracted text only flows through the
sanitization pipeline and is not persisted in plaintext on disk.
"""

import asyncio
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np  # type: ignore[import]
from PIL import Image  # type: ignore[import]

from ...utils.crypto import decrypt
from .base import BaseIngester, IngestionResult
from .ocr import run_ocr


class ImageIngester(BaseIngester):
    """Ingests encrypted image files and extracts textual content via OCR."""

    def __init__(
        self,
        languages: List[str] | None = None,
        ocr_provider: str = "paddleocr",
        ocr_provider_options: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Initialize the ingester with OCR language hints and provider.

        Args:
            languages: Optional list of OCR language codes from settings.
            ocr_provider: Provider name from settings (default: PaddleOCR).
            ocr_provider_options: Optional provider-specific options from settings.
        """
        self._languages: List[str] = languages or ["en"]
        self._ocr_provider: str = (ocr_provider or "paddleocr").strip().lower() or "paddleocr"
        self._ocr_provider_options: Dict[str, Any] = dict(ocr_provider_options or {})

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
        """Run the configured OCR provider on the given image bytes."""
        with Image.open(BytesIO(image_bytes)) as img:
            width, height = img.size
            min_dim = min(width, height)
            if min_dim < 600:
                scale = 600 / min_dim
                new_size = (int(width * scale), int(height * scale))
                img = img.resize(new_size, Image.LANCZOS)
            image_array = np.array(img.convert("RGB"))

        return run_ocr(
            self._ocr_provider,
            image_array,
            list(dict.fromkeys(self._languages + ["en"])),
            **self._ocr_provider_options,
        )

