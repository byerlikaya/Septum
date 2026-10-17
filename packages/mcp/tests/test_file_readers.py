from __future__ import annotations

"""Tests for :mod:`septum_mcp.file_readers`."""

import json
import os
from pathlib import Path

import pytest
from septum_mcp.file_readers import (
    SUPPORTED_EXTENSIONS,
    file_identity,
    read_file,
    read_file_beneath,
)


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


def test_errors_never_contain_path_or_parser_text(tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"definitely not a pdf")
    results = [
        read_file(broken),
        read_file(tmp_path / "missing.txt"),
        read_file(tmp_path),
        read_file(tmp_path / "weird-name.zip"),
    ]
    (tmp_path / "weird-name.zip").write_bytes(b"PK")
    results.append(read_file(tmp_path / "weird-name.zip"))

    for result in results:
        assert result.ok is False
        assert str(tmp_path) not in result.error
        assert "broken" not in result.error
        assert "weird-name" not in result.error
        assert ".zip" not in result.error
    assert results[0].error == "Failed to read pdf file"


def test_symlinked_final_component_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real.txt"
    real.write_text("jane@example.com", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(real)

    result = read_file(link)

    assert result.ok is False
    assert result.text == ""


def test_max_bytes_is_enforced_on_the_opened_file(tmp_path: Path) -> None:
    target = tmp_path / "big.txt"
    target.write_text("x" * 100, encoding="utf-8")

    assert read_file(target, max_bytes=10).code == "too_large"
    assert read_file(target, max_bytes=100).ok is True


def test_identity_mismatch_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("data", encoding="utf-8")

    real = file_identity(target.stat())

    for stale in (
        (-1, -1, real[2], real[3]),
        (real[0], real[1], real[2] + 1, real[3]),
        (real[0], real[1], real[2], real[3] - 1),
    ):
        result = read_file(target, expected_identity=stale)
        assert result.ok is False
        assert result.code == "changed"


def test_identity_match_reads(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("data", encoding="utf-8")

    result = read_file(target, expected_identity=file_identity(target.stat()))

    assert result.ok is True
    assert result.text == "data"


def test_read_beneath_reads_nested_regular_file(tmp_path: Path) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "c.txt").write_text("nested", encoding="utf-8")

    result = read_file_beneath(tmp_path, ("a", "b", "c.txt"))

    assert result.ok is True
    assert result.text == "nested"


def test_read_beneath_refuses_symlinked_intermediate_directory(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.txt").write_text("jane@example.com", encoding="utf-8")
    (root / "d").symlink_to(outside, target_is_directory=True)

    result = read_file_beneath(root, ("d", "x.txt"))

    assert result.ok is False
    assert result.text == ""


@pytest.mark.parametrize("parts", [(), ("..", "x.txt"), (".", "x.txt"), ("a/b",), ("",)])
def test_read_beneath_refuses_unsafe_components(tmp_path: Path, parts) -> None:
    assert read_file_beneath(tmp_path, parts).ok is False


def test_hardlinked_file_is_refused(tmp_path: Path) -> None:
    original = tmp_path / "original.txt"
    original.write_text("jane@example.com", encoding="utf-8")
    os.link(original, tmp_path / "alias.txt")

    for result in (read_file(tmp_path / "alias.txt"), read_file_beneath(tmp_path, ("alias.txt",))):
        assert result.ok is False
        assert result.code == "not_regular"


def test_fifo_is_refused_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "pipe.txt"
    os.mkfifo(fifo)

    for result in (read_file(fifo), read_file_beneath(tmp_path, ("pipe.txt",))):
        assert result.ok is False
        assert result.code == "not_regular"


def test_device_is_refused() -> None:
    result = read_file_beneath("/", ("dev", "null"))

    assert result.ok is False
    assert result.code == "not_regular"
