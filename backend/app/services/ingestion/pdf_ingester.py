from __future__ import annotations

"""PDF document ingester using PyMuPDF (fitz).

This ingester is responsible for:
    - Reading the encrypted PDF bytes from disk.
    - Decrypting them in memory using the shared AES-256-GCM utilities.
    - Extracting plain text from each page via PyMuPDF.
    - Returning an :class:`IngestionResult` with the concatenated text and
      lightweight, non-PII metadata (e.g., page count).

No raw PII is ever logged; the extracted text only flows through the
sanitization pipeline and is not persisted in plaintext on disk.
"""

import asyncio
from pathlib import Path
from typing import Any, Dict

import fitz  # type: ignore[import]

from ...utils.crypto import decrypt
from .base import BaseIngester, IngestionResult


class PdfIngester(BaseIngester):
    """Ingests encrypted PDF documents and extracts their textual content."""

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
            texts = [page.get_text() or "" for page in doc]
            full_text = "\n\n".join(texts)

            metadata: Dict[str, Any] = {
                "page_count": doc.page_count,
                "filename": filename,
            }

        return IngestionResult(text=full_text, metadata=metadata)

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
            texts = [page.get_text() or "" for page in doc]
            full_text = "\n\n".join(texts)

            metadata: Dict[str, Any] = {
                "page_count": doc.page_count,
                "mime_type": mime_type,
                "file_format": file_format,
            }

        return IngestionResult(text=full_text, metadata=metadata)

