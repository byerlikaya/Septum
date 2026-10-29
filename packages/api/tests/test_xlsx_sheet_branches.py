"""Characterization of XlsxIngester sheet handling (pins behaviour before a refactor)."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook
from septum_api.models.spreadsheet_schema import generated_technical_label
from septum_api.services.ingestion.xlsx_ingester import XlsxIngester
from septum_api.utils.crypto import encrypt

MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _encrypted_workbook(tmp_path: Path, sheets: list[tuple[str, list[list[Any]]]]) -> Path:
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, rows in sheets:
        sheet = workbook.create_sheet(title)
        for row in rows:
            sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    path = tmp_path / "book.xlsx.enc"
    path.write_bytes(encrypt(buffer.getvalue()))
    return path


async def _ingest(path: Path):
    return await XlsxIngester().ingest(path, mime_type=MIME, file_format="xlsx")


def _labels(count: int) -> list[str]:
    return [generated_technical_label(i) for i in range(count)]


@pytest.mark.asyncio
async def test_each_sheet_gets_a_header_block_with_generated_labels(tmp_path: Path) -> None:
    path = _encrypted_workbook(
        tmp_path,
        [("Alpha", [["name", "city"], ["Ann", "Oslo"]]), ("Beta", [["x"], ["y"]])],
    )

    result = await _ingest(path)

    header_alpha = "\t".join(_labels(2))
    assert result.text == (
        f"# Sheet: Alpha — columns: {' | '.join(_labels(2))}\n"
        f"{header_alpha}\nAnn\tOslo\n\n"
        f"# Sheet: Beta — columns: {_labels(1)[0]}\n{_labels(1)[0]}\ny"
    )
    assert "name" not in result.text and "Ann" in result.text


@pytest.mark.asyncio
async def test_blank_rows_count_as_rows_but_produce_no_text(tmp_path: Path) -> None:
    path = _encrypted_workbook(
        tmp_path,
        [("Gaps", [[None, None], ["a", None], [None, None], [None, "b"]])],
    )

    result = await _ingest(path)

    sheet = result.metadata["sheets"][0]
    assert sheet["row_count"] == 4
    assert sheet["column_count"] == 1
    assert result.text == f"# Sheet: Gaps — columns: {_labels(1)[0]}\n{_labels(1)[0]}\nb"


@pytest.mark.asyncio
async def test_empty_sheet_contributes_metadata_but_no_text(tmp_path: Path) -> None:
    path = _encrypted_workbook(tmp_path, [("Empty", []), ("Data", [["k"], ["v"]])])

    result = await _ingest(path)

    assert result.metadata["sheet_count"] == 2
    assert result.metadata["sheets"][0] == {"sheet_name": "Empty", "row_count": 0, "column_count": 0}
    assert result.metadata["sheets"][1]["sheet_name"] == "Data"
    assert result.text.startswith("# Sheet: Data — columns: ")
    assert "Empty" not in result.text


@pytest.mark.asyncio
async def test_sheet_of_only_blank_rows_has_counts_but_no_text(tmp_path: Path) -> None:
    path = _encrypted_workbook(tmp_path, [("Blank", [[None], [None, None]])])

    result = await _ingest(path)

    assert result.text == ""
    assert result.metadata["sheets"] == [
        {"sheet_name": "Blank", "row_count": 2, "column_count": 0}
    ]


@pytest.mark.asyncio
async def test_metadata_carries_mime_and_format(tmp_path: Path) -> None:
    path = _encrypted_workbook(tmp_path, [("S", [["h"], ["v"]])])

    result = await _ingest(path)

    assert result.metadata["mime_type"] == MIME
    assert result.metadata["file_format"] == "xlsx"
