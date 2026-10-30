from __future__ import annotations

"""XLSX spreadsheet ingester using openpyxl.

This ingester is responsible for:
    - Reading the encrypted XLSX bytes from disk.
    - Decrypting them in memory using the shared AES-256-GCM utilities.
    - Extracting plain text by iterating over sheets and rows.
    - Returning an :class:`IngestionResult` with the concatenated text and
      lightweight, non-PII metadata (e.g., sheet names, row counts).
"""

import asyncio
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Tuple

from openpyxl import load_workbook  # type: ignore[import]

from ...models.spreadsheet_schema import generated_technical_label
from ...utils.crypto import decrypt
from .base import BaseIngester, IngestionResult


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

        sheet_texts: List[str] = []
        sheet_metadata: List[Dict[str, Any]] = []

        for sheet in workbook.worksheets:
            sheet_text, row_count, header_cells = _read_sheet(sheet)
            if sheet_text:
                sheet_texts.append(_sheet_block(sheet.title, header_cells, sheet_text))

            sheet_metadata.append(
                {
                    "sheet_name": sheet.title,
                    "row_count": row_count,
                    "column_count": len(header_cells) if header_cells is not None else 0,
                }
            )

        full_text = "\n\n".join(sheet_texts)

        metadata: Dict[str, Any] = {
            "sheet_count": len(workbook.worksheets),
            "sheets": sheet_metadata,
            "mime_type": mime_type,
            "file_format": file_format,
        }

        return IngestionResult(text=full_text, metadata=metadata)


def _open_workbook(file_path: Path) -> Any:
    """Decrypt the stored XLSX at ``file_path`` and open it read-only."""
    xlsx_bytes = decrypt(file_path.read_bytes())
    return load_workbook(BytesIO(xlsx_bytes), data_only=True, read_only=True)


def _read_sheet(sheet: Any) -> Tuple[str, int, List[str] | None]:
    """Return a sheet's tab-separated text, its row count and its generated header labels.

    The first non-empty row is the header: its original text is replaced by
    generic, non-PII column labels. Empty rows are counted but produce no text.
    """
    rows_text: List[str] = []
    row_count = 0
    header_cells: List[str] | None = None

    for row in sheet.iter_rows(values_only=True):
        row_count += 1
        raw_values = _non_empty_values(row)
        if not raw_values:
            continue

        if header_cells is None:
            header_cells = [generated_technical_label(idx) for idx in range(len(raw_values))]
            rows_text.append("\t".join(header_cells))
            continue

        rows_text.append("\t".join(str(value) for value in raw_values))

    return "\n".join(rows_text), row_count, header_cells


def _non_empty_values(row: Any) -> List[Any]:
    """The cells of ``row`` that hold a value."""
    return [value for value in row if value is not None]


def _sheet_block(title: str, header_cells: List[str] | None, sheet_text: str) -> str:
    """Render one non-empty sheet, naming its generated columns when it has a header."""
    header_suffix = ""
    if header_cells:
        header_suffix = " — columns: " + " | ".join(header_cells)
    return f"# Sheet: {title}{header_suffix}\n{sheet_text}"
