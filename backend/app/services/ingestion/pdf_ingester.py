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
        ocr_provider: str = "easyocr",
        ocr_provider_options: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Initialize the ingester with OCR language hints and provider.

        Args:
            languages: Optional list of OCR language codes from settings.
            ocr_provider: Provider name from settings (currently EasyOCR-based).
            ocr_provider_options: Optional provider-specific options from settings.
        """
        self._languages: List[str] = languages or []
        self._ocr_provider: str = (ocr_provider or "easyocr").strip().lower() or "easyocr"
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
                
                # Prepend structured fields to the full text
                if fields:
                    field_lines: List[str] = []
                    field_lines.append("=== Extracted Fields ===\n")
                    
                    for field in fields:
                        field_lines.append(f"{field.label} : {field.value}")
                    
                    fields_text = "\n".join(field_lines)
                    full_text = fields_text + "\n\n" + full_text
                    
                    metadata["extracted_fields_count"] = len(fields)
            finally:
                # Clean up temp file
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

    def _extract_pages_with_optional_ocr(
        self,
        doc: fitz.Document,
    ) -> Tuple[List[str], List[float]]:
        """Extract per-page text with OCR fallback for image-only or text-poor pages."""

        texts: List[str] = []
        ocr_confidences: List[float] = []
        ocr_page_count = 0

        for index, page in enumerate(doc):
            page_text = page.get_text() or ""
            cleaned = strip_control_characters(page_text)

            # Fast path: if the existing text layer is already reasonably rich,
            # skip OCR entirely for this page to avoid expensive image-based
            # processing on long, text-heavy documents.
            stripped = cleaned.strip()
            if len(stripped) >= 200:
                texts.append(cleaned)
                continue

            ocr_text: str = ""
            ocr_confidence: Optional[float] = None

            ocr_text, ocr_confidence = self._run_ocr_on_page(page)
            if ocr_confidence is not None:
                ocr_confidences.append(ocr_confidence)
            if ocr_text:
                ocr_page_count += 1

            cleaned_ocr = strip_control_characters(ocr_text) if ocr_text else ""

            # Heuristic: prefer OCR when it produces non-empty text of comparable
            # length to the existing text layer. This is generic and does not rely
            # on language- or script-specific assumptions.
            use_ocr = False
            if cleaned_ocr.strip():
                base_len = len(stripped)
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

        # #region agent log
        try:
            import json as _json  # local import to avoid polluting module namespace
            from time import time as _time

            with open(
                "/Users/barisyerlikaya/Projects/Septum/.cursor/debug-314f97.log",
                "a",
                encoding="utf-8",
            ) as f:
                f.write(
                    _json.dumps(
                        {
                            "sessionId": "314f97",
                            "runId": "upload",
                            "hypothesisId": "U1",
                            "location": "services/ingestion/pdf_ingester.py:_extract_pages_with_optional_ocr",
                            "message": "pdf_ocr_usage",
                            "data": {
                                "page_count": len(texts),
                                "ocr_page_count": ocr_page_count,
                            },
                            "timestamp": _time(),
                        }
                    )
                    + "\n"
                )
        except Exception:
            pass
        # #endregion

        return texts, ocr_confidences

    def _run_ocr_on_page(self, page: fitz.Page) -> Tuple[str, Optional[float]]:
        """Run the configured OCR provider on a single PDF page."""
        zoom_x = 2.0
        zoom_y = 2.0
        zoom_matrix = fitz.Matrix(zoom_x, zoom_y)
        pix = page.get_pixmap(matrix=zoom_matrix, alpha=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        grayscale = image.convert("L")
        image_array = np.array(grayscale)

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
