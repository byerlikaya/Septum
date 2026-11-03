from __future__ import annotations

"""ODS (OpenDocument Spreadsheet) ingester using pandas with odf engine.

This ingester is responsible for:
    - Reading the encrypted ODS bytes from disk.
    - Decrypting them in memory using the shared AES-256-GCM utilities.
    - Extracting plain text by reading all sheets via pandas (engine=odf),
      keeping each value in its column (see :mod:`.sheet_text`).
    - Returning an :class:`IngestionResult` with the concatenated text and
      lightweight, non-PII metadata (e.g., sheet names, row counts).
"""

import asyncio
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from ...utils.crypto import decrypt
from .base import BaseIngester, IngestionResult
from .sheet_text import render_sheet


class OdsIngester(BaseIngester):
    """Ingests encrypted ODS spreadsheets and extracts their textual content."""

    async def ingest(
        self,
        file_path: Path,
        *,
        mime_type: str,
        file_format: str,
    ) -> IngestionResult:
        """Ingest the ODS at ``file_path`` and return extracted content."""

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
        """Synchronous part of ODS ingestion, run in a worker thread."""

        encrypted_bytes = file_path.read_bytes()
        ods_bytes = decrypt(encrypted_bytes)

        sheet_texts: List[str] = []
        sheet_metadata: List[Dict[str, Any]] = []

        with pd.ExcelFile(BytesIO(ods_bytes), engine="odf") as xl:
            for sheet_name in xl.sheet_names:
                # The raw grid, positions and value types kept: no header row
                # is taken out and integers are not widened to floats.
                df = pd.read_excel(xl, sheet_name=sheet_name, header=None, dtype=object)
                sheet_text, row_count, header_cells = render_sheet(
                    str(sheet_name), df.itertuples(index=False, name=None)
                )
                if sheet_text:
                    sheet_texts.append(sheet_text)
                sheet_metadata.append(
                    {
                        "sheet_name": sheet_name,
                        "row_count": row_count,
                        "column_count": len(header_cells) if header_cells is not None else 0,
                    }
                )

        full_text = "\n\n".join(sheet_texts)

        metadata: Dict[str, Any] = {
            "sheet_count": len(sheet_metadata),
            "sheets": sheet_metadata,
            "mime_type": mime_type,
            "file_format": file_format,
        }

        return IngestionResult(text=full_text, metadata=metadata)
