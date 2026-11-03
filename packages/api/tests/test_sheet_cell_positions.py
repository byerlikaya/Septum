"""Spreadsheet text keeps every value in its own column.

Empty cells used to be dropped before the row was joined, so a value after
a gap moved left into the wrong generated column, and the column count
came from the non-empty cells of the first row only. Both XLSX and ODS now
keep positions: empty cells stay as empty fields, the sheet width is its
widest row, leading columns empty in every row are trimmed, and trailing
empty fields are not written.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from openpyxl import Workbook

from septum_api.models.spreadsheet_schema import generated_technical_label
from septum_api.services.ingestion.ods_ingester import OdsIngester
from septum_api.services.ingestion.xlsx_ingester import XlsxIngester
from septum_api.utils.crypto import encrypt

ROWS: list[list[Any]] = [
    ["Name", None, "Salary"],
    ["Ali", None, 42],
    [None, "Oslo", None],
    ["Ayşe", "Bergen", 7, None, "note"],
]


def _xlsx(rows: list[list[Any]], start_col: int = 1) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Data"
    for r, row in enumerate(rows, start=1):
        for c, value in enumerate(row, start=start_col):
            if value is not None:
                sheet.cell(row=r, column=c, value=value)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _ods(rows: list[list[Any]], start_col: int = 0) -> bytes:
    width = max(len(row) for row in rows)
    frame = pd.DataFrame([row + [None] * (width - len(row)) for row in rows], dtype=object)
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="odf") as writer:
        frame.to_excel(writer, sheet_name="Data", index=False, header=False, startcol=start_col)
    return buffer.getvalue()


async def _ingest(tmp_path: Path, kind: str, data: bytes):
    path = tmp_path / f"book.{kind}.enc"
    path.write_bytes(encrypt(data))
    ingester = XlsxIngester() if kind == "xlsx" else OdsIngester()
    return await ingester.ingest(path, mime_type="x", file_format=kind)


def _labels(count: int) -> list[str]:
    return [generated_technical_label(i) for i in range(count)]


def _expected(width: int, lines: list[str]) -> str:
    labels = _labels(width)
    return "\n".join([f"# Sheet: Data — columns: {' | '.join(labels)}", "\t".join(labels), *lines])


_LINES = ["Ali\t\t42", "\tOslo", "Ayşe\tBergen\t7\t\tnote"]


@pytest.mark.parametrize(("kind", "build"), [("xlsx", _xlsx), ("ods", _ods)])
async def test_values_keep_their_columns(tmp_path: Path, kind: str, build) -> None:
    result = await _ingest(tmp_path, kind, build(ROWS))

    assert result.text == _expected(5, _LINES)
    assert result.metadata["sheets"][0]["column_count"] == 5
    assert "Name" not in result.text and "Salary" not in result.text


@pytest.mark.parametrize(("kind", "build", "start"), [("xlsx", _xlsx, 3), ("ods", _ods, 2)])
async def test_leading_empty_columns_are_trimmed(tmp_path: Path, kind: str, build, start: int) -> None:
    result = await _ingest(tmp_path, kind, build(ROWS, start))

    assert result.text == _expected(5, _LINES)


async def test_ods_integers_stay_integers(tmp_path: Path) -> None:
    result = await _ingest(tmp_path, "ods", _ods([["h1", "h2"], [42, None], [None, 3.5]]))

    assert result.text.splitlines()[2:] == ["42", "\t3.5"]


@pytest.mark.parametrize(("kind", "build"), [("xlsx", _xlsx), ("ods", _ods)])
async def test_header_with_a_gap_counts_every_column(tmp_path: Path, kind: str, build) -> None:
    result = await _ingest(tmp_path, kind, build([["a", None, "c"], ["1", "2", "3"]]))

    assert result.metadata["sheets"][0]["column_count"] == 3
    assert result.text.splitlines()[2] == "1\t2\t3"
