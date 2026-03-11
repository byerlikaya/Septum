from __future__ import annotations

"""PDF document ingester using PyMuPDF (fitz).

This ingester is responsible for:
    - Reading the encrypted PDF bytes from disk.
    - Decrypting them in memory using the shared AES-256-GCM utilities.
    - Extracting plain text from each page via PyMuPDF, with an optional OCR
      fallback for image-only pages.
    - Returning an :class:`IngestionResult` with the concatenated text and
      lightweight, non-PII metadata (e.g., page count).

No raw PII is ever logged; the extracted text only flows through the
sanitization pipeline and is not persisted in plaintext on disk.
"""

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import fitz  # type: ignore[import]
import numpy as np  # type: ignore[import]
from PIL import Image  # type: ignore[import]

from ...utils.crypto import decrypt
from ...utils.device import get_device
from ...utils.text_utils import strip_control_characters
from .base import BaseIngester, IngestionResult


class PdfIngester(BaseIngester):
    """Ingests encrypted PDF documents and extracts their textual content."""

    def __init__(self, languages: Optional[List[str]] = None) -> None:
        """Initialize the ingester with optional OCR language hints.

        Args:
            languages: Optional list of EasyOCR language codes. When provided,
                these are used as hints for OCR fallback on image-only pages.
        """

        self._languages: List[str] = languages or []

    async def extract(self, data: bytes, filename: str) -> IngestionResult:
        """Extract text from raw (unencrypted) PDF bytes.

        This helper exists primarily for ad-hoc, local usage such as:

            with open("sample.pdf", "rb") as f:
                result = await PdfIngester().extract(f.read(), "sample.pdf")

        It does not perform any encryption/decryption and MUST NOT be used
        for files managed by the main ingestion pipeline, where all files are
        stored encrypted on disk.
        """

        return await asyncio.to_thread(self._extract_sync, data, filename)

    def _extract_sync(self, data: bytes, filename: str) -> IngestionResult:
        """Synchronous helper for :meth:`extract`, run in a worker thread."""

        with fitz.open(stream=data, filetype="pdf") as doc:
            texts, ocr_confidences = self._extract_pages_with_optional_ocr(doc)
            full_text = "\n\n".join(texts)

            metadata: Dict[str, Any] = {
                "page_count": doc.page_count,
                "filename": filename,
            }

        avg_confidence = (
            float(sum(ocr_confidences) / len(ocr_confidences))
            if ocr_confidences
            else None
        )

        return IngestionResult(
            text=full_text,
            metadata=metadata,
            confidence=avg_confidence,
        )

    async def ingest(
        self,
        file_path: Path,
        *,
        mime_type: str,
        file_format: str,
    ) -> IngestionResult:
        """Ingest the PDF at ``file_path`` and return extracted content."""

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
        """Synchronous part of PDF ingestion, run in a worker thread."""

        encrypted_bytes = file_path.read_bytes()
        pdf_bytes = decrypt(encrypted_bytes)

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            texts, ocr_confidences = self._extract_pages_with_optional_ocr(doc)
            full_text = "\n\n".join(texts)

            metadata: Dict[str, Any] = {
                "page_count": doc.page_count,
                "mime_type": mime_type,
                "file_format": file_format,
            }

        avg_confidence = (
            float(sum(ocr_confidences) / len(ocr_confidences))
            if ocr_confidences
            else None
        )

        return IngestionResult(
            text=full_text,
            metadata=metadata,
            confidence=avg_confidence,
        )

    def _extract_pages_with_optional_ocr(
        self,
        doc: fitz.Document,
    ) -> Tuple[List[str], List[float]]:
        """Extract per-page text with OCR fallback for image-only pages."""

        texts: List[str] = []
        ocr_confidences: List[float] = []
        reader = None

        for index, page in enumerate(doc):
            page_text = page.get_text() or ""
            cleaned = strip_control_characters(page_text)

            ocr_text: str = ""
            ocr_confidence: Optional[float] = None

            # Always consider an OCR pass as an alternative signal, even when the
            # text layer is non-empty. This allows correcting mis-encoded text
            # while keeping the original layer for well-formed documents.
            if reader is None:
                reader = self._build_ocr_reader()
            if reader is not None:
                ocr_text, ocr_confidence = self._run_ocr_on_page(page, reader)
                if ocr_confidence is not None:
                    ocr_confidences.append(ocr_confidence)

            cleaned_ocr = strip_control_characters(ocr_text) if ocr_text else ""

            # Heuristic: prefer OCR when it produces non-empty text of comparable
            # length to the existing text layer. This is generic and does not rely
            # on language- or script-specific assumptions.
            use_ocr = False
            if cleaned_ocr.strip():
                base_len = len(cleaned.strip())
                ocr_len = len(cleaned_ocr.strip())
                if base_len == 0:
                    use_ocr = True
                else:
                    ratio = float(ocr_len) / float(base_len)
                    if 0.5 <= ratio <= 2.0:
                        use_ocr = True

            if use_ocr:
                cleaned = cleaned_ocr

            texts.append(cleaned)

        return texts, ocr_confidences

    def _build_ocr_reader(self) -> Any:
        """Build an EasyOCR reader instance for OCR fallback."""

        import easyocr  # type: ignore[import]

        device = get_device()
        use_gpu = device == "cuda"

        # Preserve user-configured language order and ensure English is available
        # as a general fallback without dominating the selection order.
        base_languages = list(dict.fromkeys(self._languages or []))
        if "en" not in base_languages:
            base_languages.append("en")
        effective_languages = base_languages or ["en"]

        try:
            return easyocr.Reader(effective_languages, gpu=use_gpu)
        except ValueError:
            # Prefer non-default languages first, then fall back to English-only.
            primary_languages = [lang for lang in effective_languages if lang != "en"]
            if "en" in effective_languages:
                primary_languages.append("en")

            for lang in primary_languages:
                try:
                    candidate_languages = ["en"] if lang == "en" else [lang, "en"]
                    return easyocr.Reader(candidate_languages, gpu=use_gpu)
                except ValueError:
                    continue

            return easyocr.Reader(["en"], gpu=use_gpu)

    def _run_ocr_on_page(
        self,
        page: fitz.Page,
        reader: Any,
    ) -> Tuple[str, Optional[float]]:
        """Run OCR on a single PDF page using the provided reader."""

        # Render the page at higher resolution to improve OCR quality.
        zoom_x = 2.0
        zoom_y = 2.0
        zoom_matrix = fitz.Matrix(zoom_x, zoom_y)
        pix = page.get_pixmap(matrix=zoom_matrix, alpha=False)
        mode = "RGB"
        image = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
        # Use grayscale to reduce noise while preserving character shapes.
        grayscale = image.convert("L")
        image_array = np.array(grayscale)

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

        joined_text = "\n".join(texts)
        avg_confidence = (
            float(sum(confidences) / len(confidences)) if confidences else None
        )
        return joined_text, avg_confidence
