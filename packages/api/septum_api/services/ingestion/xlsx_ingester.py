from __future__ import annotations

"""XLSX spreadsheet ingester using openpyxl.

This ingester is responsible for:
    - Reading the encrypted XLSX bytes from disk.
    - Decrypting them in memory using the shared AES-256-GCM utilities.
    - Extracting plain text by iterating over sheets and rows, keeping each
      value in its column (see :mod:`.sheet_text`).
    - Returning an :class:`IngestionResult` with the concatenated text and
      lightweight, non-PII metadata (e.g., sheet names, row counts).
"""

import asyncio
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List

from openpyxl import load_workbook  # type: ignore[import]

from ...utils.crypto import decrypt
from .base import BaseIngester, IngestionResult, TextBlock, join_blocks
from .sheet_text import render_sheet


class XlsxIngester(BaseIngester):
    """Ingests encrypted XLSX spreadsheets and extracts their textual content."""

    async def ingest(
        self,
        file_path: Path,
        *,
        mime_type: str,
        file_format: str,
    ) -> IngestionResult:
        """Ingest the XLSX at ``file_path`` and return extracted content."""

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
        """Synchronous part of XLSX ingestion, run in a worker thread."""

        workbook = _open_workbook(file_path)

        blocks: List[TextBlock] = []
        sheet_metadata: List[Dict[str, Any]] = []

        for sheet in workbook.worksheets:
            sheet_blocks, row_count, header_cells = render_sheet(
                sheet.title, sheet.iter_rows(values_only=True)
            )
            if sheet_blocks:
                blocks.extend(sheet_blocks)

            sheet_metadata.append(
                {
                    "sheet_name": sheet.title,
                    "row_count": row_count,
                    "column_count": len(header_cells) if header_cells is not None else 0,
                }
            )

        full_text = join_blocks(blocks)

        metadata: Dict[str, Any] = {
            "sheet_count": len(workbook.worksheets),
            "sheets": sheet_metadata,
            "mime_type": mime_type,
            "file_format": file_format,
        }

        return IngestionResult(text=full_text, metadata=metadata, blocks=blocks)


def _open_workbook(file_path: Path) -> Any:
    """Decrypt the stored XLSX at ``file_path`` and open it read-only."""
    xlsx_bytes = decrypt(file_path.read_bytes())
    return load_workbook(BytesIO(xlsx_bytes), data_only=True, read_only=True)
