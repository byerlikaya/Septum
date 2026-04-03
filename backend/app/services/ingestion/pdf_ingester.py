from __future__ import annotations

"""PDF document ingester using PyMuPDF (fitz).

This ingester is responsible for:
    - Reading the encrypted PDF bytes from disk.
    - Decrypting them in memory using the shared AES-256-GCM utilities.
    - Extracting plain text from each page via PyMuPDF, with an optional OCR
      fallback using the shared OCR provider layer.
    - Returning an :class:`IngestionResult` with the concatenated text and
      lightweight, non-PII metadata (e.g., page count).

No raw PII is ever logged; the extracted text only flows through the
sanitization pipeline and is not persisted in plaintext on disk.
"""

import asyncio
import io
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import fitz  # type: ignore[import]
import numpy as np  # type: ignore[import]
from PIL import Image  # type: ignore[import]

from ...utils.crypto import decrypt
from ...utils.text_utils import strip_control_characters
from .base import BaseIngester, IngestionResult
from .ocr import run_ocr
from .table_extractor import TableFieldExtractor


class PdfIngester(BaseIngester):
    """Ingests encrypted PDF documents and extracts their textual content."""

    def __init__(
        self,
        languages: Optional[List[str]] = None,
        ocr_provider: str = "paddleocr",
        ocr_provider_options: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Initialize the ingester with OCR language hints and provider.

        Args:
            languages: Optional list of OCR language codes from settings.
            ocr_provider: Provider name from settings (default: PaddleOCR).
            ocr_provider_options: Optional provider-specific options from settings.
        """
        self._languages: List[str] = languages or []
        self._ocr_provider: str = (ocr_provider or "paddleocr").strip().lower() or "paddleocr"
        self._ocr_provider_options: Dict[str, Any] = dict(ocr_provider_options or {})

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

        # Phase 2: Extract structured fields with pdfplumber
        # We need to write decrypted PDF bytes to a temp file for pdfplumber
        try:
            import tempfile
            
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(pdf_bytes)
                tmp_path = tmp.name
            
            try:
                extractor = TableFieldExtractor()
                fields, tables = extractor.extract_from_pdf(tmp_path)
                
                if fields:
                    fields_text_raw = "\n".join(
                        f"{f.label} : {f.value}" for f in fields
                    )
                    if "(cid:" not in fields_text_raw:
                        field_lines = ["=== Extracted Fields ===\n"] + [
                            f"{f.label} : {f.value}" for f in fields
                        ]
                        full_text = "\n".join(field_lines) + "\n\n" + full_text
                        metadata["extracted_fields_count"] = len(fields)
            finally:
                import os
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        except Exception:
            # If field extraction fails, continue with text-only
            pass

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

    _MIN_IMAGE_DIMENSION = 150

    def _extract_pages_with_optional_ocr(
        self,
        doc: fitz.Document,
    ) -> Tuple[List[str], List[float]]:
        """Extract per-page text, plus OCR any embedded images.

        Strategy per page:
        1. If the text layer is corrupt (CID / U+FFFD) → full-page OCR.
        2. Otherwise → use extracted text + OCR any significant images
           on the page (≥150×150 px) and append their text.
        """
        texts: List[str] = []
        ocr_confidences: List[float] = []

        for page in doc:
            page_text = page.get_text() or ""
            cleaned = strip_control_characters(page_text).strip()

            if self._text_looks_corrupt(cleaned):
                ocr_text, conf = self._run_ocr_on_page(page)
                if conf is not None:
                    ocr_confidences.append(conf)
                texts.append(strip_control_characters(ocr_text) if ocr_text else cleaned)
                continue

            image_texts, image_confs = self._ocr_page_images(page, doc)
            ocr_confidences.extend(image_confs)

            if image_texts:
                combined = cleaned + "\n\n" + "\n\n".join(image_texts)
                texts.append(combined)
            else:
                texts.append(cleaned)

        return texts, ocr_confidences

    def _ocr_page_images(
        self,
        page: fitz.Page,
        doc: fitz.Document,
    ) -> Tuple[List[str], List[float]]:
        """Extract and OCR significant images embedded in a page."""
        image_texts: List[str] = []
        image_confs: List[float] = []

        for img_info in page.get_images(full=True):
            xref = img_info[0]
            width = img_info[2]
            height = img_info[3]

            if width < self._MIN_IMAGE_DIMENSION or height < self._MIN_IMAGE_DIMENSION:
                continue

            try:
                img_data = doc.extract_image(xref)
                if img_data is None:
                    continue

                image = Image.open(io.BytesIO(img_data["image"]))
                if image.mode != "RGB":
                    image = image.convert("RGB")
                image_array = np.array(image)

                languages = list(dict.fromkeys(self._languages or []))
                if "en" not in languages:
                    languages.append("en")

                ocr_texts, confs = run_ocr(
                    self._ocr_provider,
                    image_array,
                    languages or ["en"],
                    **self._ocr_provider_options,
                )
                if ocr_texts:
                    image_texts.append("\n".join(ocr_texts))
                    image_confs.extend(confs)
            except Exception:
                continue

        return image_texts, image_confs

    @staticmethod
    def _text_looks_corrupt(text: str) -> bool:
        """Detect text with broken font encoding.

        Returns True when the extracted text layer is likely unusable:
        1. CID references indicate unresolved glyph mappings.
        2. Replacement characters (U+FFFD) indicate failed decoding.

        The rawdict span check was removed because many valid fonts
        (especially embedded subsets) return empty span texts in rawdict
        while ``get_text()`` extracts content correctly.  This caused
        false-positive corrupt detection and unnecessary OCR on every
        page, adding ~1.5 GB of PaddlePaddle memory and minutes of
        processing time per document.
        """
        if "(cid:" in text:
            return True
        if "\ufffd" in text:
            return True

        return False

    def _run_ocr_on_page(self, page: fitz.Page) -> Tuple[str, Optional[float]]:
        """Run the configured OCR provider on a single PDF page."""
        zoom_x = 4.0
        zoom_y = 4.0
        zoom_matrix = fitz.Matrix(zoom_x, zoom_y)
        pix = page.get_pixmap(matrix=zoom_matrix, alpha=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        image_array = np.array(image)

        languages = list(dict.fromkeys(self._languages or []))
        if "en" not in languages:
            languages.append("en")
        effective = languages or ["en"]

        texts, confidences = run_ocr(
            self._ocr_provider,
            image_array,
            effective,
            **self._ocr_provider_options,
        )
        joined_text = "\n".join(texts)
        avg_confidence = (
            float(sum(confidences) / len(confidences)) if confidences else None
        )
        return joined_text, avg_confidence
