from __future__ import annotations

"""Tests for :mod:`septum_mcp.file_readers`."""

import json
from pathlib import Path

from septum_mcp.file_readers import SUPPORTED_EXTENSIONS, read_file


def test_read_plain_text_file(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("Contact jane@example.com please.\n", encoding="utf-8")

    result = read_file(target)

    assert result.ok is True
    assert "jane@example.com" in result.text
    assert result.format == "txt"
    assert result.error == ""


def test_read_markdown_file(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    target.write_text("# Title\n\nEmail: jane@example.com", encoding="utf-8")

    result = read_file(target)

    assert result.ok is True
    assert result.format == "md"
    assert "jane@example.com" in result.text


def test_read_csv_file_joins_cells_with_tabs(tmp_path: Path) -> None:
    target = tmp_path / "people.csv"
    target.write_text("name,email\nJane,jane@example.com\n", encoding="utf-8")

    result = read_file(target)

    assert result.ok is True
    assert "Jane\tjane@example.com" in result.text


def test_read_json_file_preserves_string_values(tmp_path: Path) -> None:
    target = tmp_path / "user.json"
    target.write_text(
        json.dumps({"email": "jane@example.com", "name": "Jane"}), encoding="utf-8"
    )

    result = read_file(target)

    assert result.ok is True
    assert "jane@example.com" in result.text
    assert "Jane" in result.text


def test_read_json_file_falls_back_to_raw_on_parse_error(tmp_path: Path) -> None:
    target = tmp_path / "broken.json"
    target.write_text("{ not json } jane@example.com", encoding="utf-8")

    result = read_file(target)

    assert result.ok is True
    assert "jane@example.com" in result.text


def test_missing_file_returns_error() -> None:
    result = read_file("/does/not/exist.txt")

    assert result.ok is False
    assert "not found" in result.error.lower()


def test_unsupported_extension_lists_supported_formats(tmp_path: Path) -> None:
    target = tmp_path / "archive.zip"
    target.write_bytes(b"PK\x03\x04")

    result = read_file(target)

    assert result.ok is False
    assert ".txt" in result.error
    assert ".pdf" in result.error


def test_directory_path_is_rejected(tmp_path: Path) -> None:
    result = read_file(tmp_path)

    assert result.ok is False
    assert "not a regular file" in result.error.lower()


def test_docx_reader_handles_paragraphs(tmp_path: Path) -> None:
    docx = __import__("docx")
    document = docx.Document()
    document.add_paragraph("Contact jane@example.com today.")
    document.add_paragraph("Follow up with bob@example.org later.")
    target = tmp_path / "doc.docx"
    document.save(str(target))

    result = read_file(target)

    assert result.ok is True
    assert result.format == "docx"
    assert "jane@example.com" in result.text
    assert "bob@example.org" in result.text


def test_pdf_reader_extracts_text(tmp_path: Path) -> None:
    from reportlab.lib.pagesizes import letter  # type: ignore
    from reportlab.pdfgen import canvas  # type: ignore

    target = tmp_path / "doc.pdf"
    pdf = canvas.Canvas(str(target), pagesize=letter)
    pdf.drawString(100, 750, "Contact jane@example.com today.")
    pdf.save()

    result = read_file(target)

    assert result.ok is True
    assert result.format == "pdf"
    assert "jane@example.com" in result.text


def test_corrupt_pdf_returns_error(tmp_path: Path) -> None:
    target = tmp_path / "broken.pdf"
    target.write_bytes(b"definitely not a pdf")

    result = read_file(target)

    assert result.ok is False
    assert "pdf" in result.error.lower()


def test_supported_extensions_exports_tuple() -> None:
    assert ".txt" in SUPPORTED_EXTENSIONS
    assert ".pdf" in SUPPORTED_EXTENSIONS
    assert ".docx" in SUPPORTED_EXTENSIONS
    assert isinstance(SUPPORTED_EXTENSIONS, tuple)
