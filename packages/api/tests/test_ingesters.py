from __future__ import annotations

from io import BytesIO
from pathlib import Path

import fitz  # type: ignore[import]
import pytest
from docx import Document as DocxDocument  # type: ignore[import]
from openpyxl import Workbook  # type: ignore[import]

from septum_api.services.ingestion.audio_ingester import _segment_blocks
from septum_api.services.ingestion.base import IngestionResult, TextBlock, join_blocks
from septum_api.services.ingestion.docx_ingester import DocxIngester
from septum_api.services.ingestion.ods_ingester import OdsIngester
from septum_api.services.ingestion.pdf_ingester import PdfIngester
from septum_api.services.ingestion.xlsx_ingester import XlsxIngester
from septum_api.utils.crypto import encrypt


def _create_sample_pdf_bytes(text: str) -> bytes:
    """Create a simple one-page PDF containing the given text."""

    doc = fitz.open()
    try:
        page = doc.new_page()
        page.insert_text((72, 72), text)
        return doc.tobytes()
    finally:
        doc.close()


def _create_sample_docx_bytes(text: str) -> bytes:
    """Create a simple DOCX document containing the given text."""

    document = DocxDocument()
    document.add_paragraph(text)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _create_sample_xlsx_bytes(values: list[list[str]]) -> bytes:
    """Create a simple XLSX workbook from a 2D list of strings."""

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    for row in values:
        ws.append(row)

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _write_encrypted_file(tmp_path: Path, filename: str, plaintext: bytes) -> Path:
    """Encrypt ``plaintext`` and write it to ``tmp_path/filename``."""

    encrypted = encrypt(plaintext)
    file_path = tmp_path / filename
    file_path.write_bytes(encrypted)
    return file_path


@pytest.mark.asyncio
async def test_pdf_ingester_extracts_text(tmp_path: Path) -> None:
    """PdfIngester should correctly extract text from a simple PDF."""

    sample_text = "Hello from PDF ingester"
    pdf_bytes = _create_sample_pdf_bytes(sample_text)
    encrypted_path = _write_encrypted_file(tmp_path, "sample.pdf.enc", pdf_bytes)

    ingester = PdfIngester()
    result = await ingester.ingest(
        encrypted_path,
        mime_type="application/pdf",
        file_format="pdf",
    )

    assert sample_text in result.text
    assert result.metadata.get("page_count") == 1
    assert result.metadata.get("mime_type") == "application/pdf"
    assert result.metadata.get("file_format") == "pdf"
    assert join_blocks(result.blocks) == result.text
    assert [(b.kind, b.page) for b in result.blocks] == [("page", 1)]


@pytest.mark.asyncio
async def test_pdf_ingester_blocks_one_per_page(tmp_path: Path) -> None:
    """Every page is a block carrying its page number; text is their join."""

    doc = fitz.open()
    for text in ("first page", "second page", "third page"):
        doc.new_page().insert_text((72, 72), text)
    path = _write_encrypted_file(tmp_path, "multi.pdf.enc", doc.tobytes())
    doc.close()

    result = await PdfIngester().ingest(path, mime_type="application/pdf", file_format="pdf")

    assert join_blocks(result.blocks) == result.text
    pages = [b for b in result.blocks if b.kind == "page"]
    assert [b.page for b in pages] == [1, 2, 3]
    assert "second page" in pages[1].text


@pytest.mark.asyncio
async def test_docx_ingester_extracts_text(tmp_path: Path) -> None:
    """DocxIngester should correctly extract text from a simple DOCX."""

    sample_text = "Hello from DOCX ingester"
    docx_bytes = _create_sample_docx_bytes(sample_text)
    encrypted_path = _write_encrypted_file(tmp_path, "sample.docx.enc", docx_bytes)

    ingester = DocxIngester()
    result = await ingester.ingest(
        encrypted_path,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        file_format="docx",
    )

    assert sample_text in result.text
    assert result.metadata.get("paragraph_count") == 1
    assert result.metadata.get("file_format") == "docx"
    assert join_blocks(result.blocks) == result.text


@pytest.mark.asyncio
async def test_docx_ingester_blocks_headings_paragraphs_tables(tmp_path: Path) -> None:
    """Headings come from styles; paragraphs and tables follow; join equals text."""

    document = DocxDocument()
    document.add_heading("Chapter One", level=1)
    document.add_paragraph("Body line one")
    document.add_paragraph("")
    document.add_paragraph("Body line two")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "left"
    table.rows[0].cells[1].text = "right"
    document.sections[0].header.paragraphs[0].text = "Running header"
    buffer = BytesIO()
    document.save(buffer)
    path = _write_encrypted_file(tmp_path, "blocks.docx.enc", buffer.getvalue())

    result = await DocxIngester().ingest(path, mime_type="x", file_format="docx")

    assert join_blocks(result.blocks) == result.text
    kinds = [b.kind for b in result.blocks]
    assert kinds[0] == "heading"
    assert result.blocks[0].text == "Chapter One"
    assert "table" in kinds
    assert kinds[-1] == "part"
    assert result.blocks[-1].text == "Running header"


@pytest.mark.asyncio
async def test_xlsx_ingester_extracts_text(tmp_path: Path) -> None:
    """XlsxIngester should correctly extract text from a simple XLSX."""

    values = [["Name", "Value"], ["foo", "42"]]
    sample_value = "foo"
    xlsx_bytes = _create_sample_xlsx_bytes(values)
    encrypted_path = _write_encrypted_file(tmp_path, "sample.xlsx.enc", xlsx_bytes)

    ingester = XlsxIngester()
    result = await ingester.ingest(
        encrypted_path,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        file_format="xlsx",
    )

    assert sample_value in result.text
    assert result.metadata.get("sheet_count") == 1
    sheets_meta = result.metadata.get("sheets") or []
    assert len(sheets_meta) == 1
    assert sheets_meta[0].get("sheet_name") == "Sheet1"
    assert join_blocks(result.blocks) == result.text
    assert {b.sheet for b in result.blocks} == {"Sheet1"}
    assert [b.kind for b in result.blocks] == ["sheet_header", "rows"]


@pytest.mark.asyncio
async def test_xlsx_ingester_blocks_carry_sheet_names(tmp_path: Path) -> None:
    """Each sheet starts with a header block; its rows keep the sheet name."""

    wb = Workbook()
    wb.active.title = "First"
    wb["First"].append(["a", "b"])
    wb["First"].append(["1", "2"])
    wb.create_sheet("Second").append(["c"])
    wb["Second"].append(["3"])
    buffer = BytesIO()
    wb.save(buffer)
    path = _write_encrypted_file(tmp_path, "two.xlsx.enc", buffer.getvalue())

    result = await XlsxIngester().ingest(path, mime_type="x", file_format="xlsx")

    assert join_blocks(result.blocks) == result.text
    headers = [b for b in result.blocks if b.kind == "sheet_header"]
    assert [b.sheet for b in headers] == ["First", "Second"]
    row_blocks = [b for b in result.blocks if b.kind == "rows"]
    assert [b.sheet for b in row_blocks] == ["First", "Second"]
    for block in row_blocks:
        lines = block.text.split("\n")
        starts = list(block.row_offsets)
        assert [block.text[a:b - 1] for a, b in zip(starts, starts[1:] + [len(block.text) + 1])] == lines


def test_result_without_blocks_defaults_to_one_block() -> None:
    """An ingester that gives no blocks (e.g. image OCR) gets one block with the whole text."""

    result = IngestionResult(text="line one\nline two", metadata={})

    assert result.blocks == [TextBlock(text="line one\nline two", kind="text")]
    assert join_blocks(result.blocks) == result.text
    assert IngestionResult(text="", metadata={}).blocks == []


@pytest.mark.parametrize(
    ("text", "segments"),
    [
        (
            " Hello there. General Kenobi.",
            [
                {"text": " Hello there.", "start": 0.0, "end": 1.5},
                {"text": " General Kenobi.", "start": 1.5, "end": 3.0},
            ],
        ),
        (
            "Hello there. General Kenobi.",
            [
                {"text": " Hello there.", "start": 0.0, "end": 1.5},
                {"text": " General Kenobi.", "start": 31.5, "end": 33.0},
            ],
        ),
        ("untimed text", []),
        ("not found", [{"text": "other", "start": 1.0, "end": 2.0}]),
        ("", []),
    ],
)
def test_audio_segment_blocks_join_to_text(text: str, segments: list[dict]) -> None:
    """Segment blocks reproduce the transcript exactly and carry their times."""

    blocks = _segment_blocks(text, segments)

    assert join_blocks(blocks) == text
    if text and segments and text.find(segments[0]["text"].strip()) >= 0:
        assert [(b.t_start, b.t_end) for b in blocks] == [
            (s["start"], s["end"]) for s in segments
        ]


def _create_sample_ods_bytes(values: list[list[str]]) -> bytes:
    """Create a simple ODS workbook from a 2D list of strings using pandas.

    The first inner list is treated as column headers; subsequent rows are data.
    """

    import pandas as pd

    if not values:
        raise ValueError("values must contain at least a header row")

    headers = values[0]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=headers)
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="odf") as writer:
        df.to_excel(writer, sheet_name="Sheet1", index=False, header=True)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_ods_ingester_extracts_text(tmp_path: Path) -> None:
    """OdsIngester should correctly extract text from a simple ODS."""

    values = [["Name", "Salary"], ["bar", "99"]]
    header_in_sheet_line = "columns: COLUMN_1 | COLUMN_2"
    sample_value = "bar"
    ods_bytes = _create_sample_ods_bytes(values)
    encrypted_path = _write_encrypted_file(tmp_path, "sample.ods.enc", ods_bytes)

    ingester = OdsIngester()
    result = await ingester.ingest(
        encrypted_path,
        mime_type="application/vnd.oasis.opendocument.spreadsheet",
        file_format="ods",
    )

    assert header_in_sheet_line in result.text
    assert sample_value in result.text
    assert result.metadata.get("sheet_count") == 1
    sheets_meta = result.metadata.get("sheets") or []
    assert len(sheets_meta) == 1
    assert sheets_meta[0].get("sheet_name") == "Sheet1"
    assert join_blocks(result.blocks) == result.text
    assert {b.sheet for b in result.blocks} == {"Sheet1"}


_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _legacy_docx_text(document) -> str:
    """The flat DOCX text as produced before blocks existed (body, then distinct parts)."""

    from septum_api.services.ingestion import docx_ingester as dx

    sections = [dx._part_text(document.element.body)]
    for element in dx._related_part_elements(document):
        text = dx._part_text(element)
        if text and text not in sections:
            sections.append(text)
    return "\n\n".join(section for section in sections if section)


def _body_xml(document, xml: str) -> None:
    """Insert raw WordprocessingML children before the section properties."""

    from docx.oxml import parse_xml

    body = document.element.body
    sect = body[-1] if body[-1].tag.endswith("sectPr") else None
    for child in parse_xml(f'<w:x xmlns:w="{_W_NS}">{xml}</w:x>'):
        if sect is not None:
            sect.addprevious(child)
        else:
            body.append(child)


def _docx_variant_plain():
    document = DocxDocument()
    document.add_paragraph("  leading spaces kept? ")
    document.add_paragraph("")
    document.add_paragraph("")
    document.add_paragraph("tab\there and break")
    return document


def _docx_variant_leading_trailing_blanks():
    document = DocxDocument()
    document.add_paragraph("")
    document.add_paragraph("   ")
    document.add_heading("Title", level=0)
    document.add_paragraph("body")
    document.add_paragraph("")
    document.add_paragraph("  ")
    return document


def _docx_variant_nested_table():
    document = DocxDocument()
    document.add_paragraph("before")
    outer = document.add_table(rows=2, cols=2)
    outer.rows[0].cells[0].text = "a"
    outer.rows[0].cells[1].text = "b"
    inner = outer.rows[1].cells[0].add_table(rows=1, cols=2)
    inner.rows[0].cells[0].text = "n1"
    inner.rows[0].cells[1].text = "n2"
    outer.rows[1].cells[1].text = "d"
    document.add_paragraph("after")
    return document


def _docx_variant_table_last():
    document = DocxDocument()
    document.add_paragraph("intro")
    table = document.add_table(rows=1, cols=1)
    table.rows[0].cells[0].text = "last cell"
    return document


def _docx_variant_content_controls_and_textbox():
    document = DocxDocument()
    document.add_paragraph("lead")
    _body_xml(
        document,
        '<w:sdt><w:sdtContent><w:p><w:r><w:t>block control one</w:t></w:r></w:p>'
        "<w:p><w:r><w:t>block control two</w:t></w:r></w:p></w:sdtContent></w:sdt>"
        '<w:p><w:sdt><w:sdtContent><w:r><w:t>inline control</w:t></w:r></w:sdtContent></w:sdt></w:p>'
        '<w:p><w:r><w:t>x</w:t><w:br/><w:t>y</w:t><w:cr/></w:r></w:p>',
    )
    _body_xml(
        document,
        '<w:p xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
        "<mc:AlternateContent><mc:Choice><w:r><w:t>textbox choice</w:t></w:r>"
        "<w:txbxContent><w:p><w:r><w:t>boxed</w:t></w:r></w:p></w:txbxContent></mc:Choice>"
        "<mc:Fallback><w:r><w:t>textbox fallback</w:t></w:r></mc:Fallback>"
        "</mc:AlternateContent></w:p>",
    )
    document.add_paragraph("tail")
    return document


def _docx_variant_header_footer_duplicates():
    document = DocxDocument()
    document.add_paragraph("body")
    document.sections[0].header.paragraphs[0].text = "same"
    document.sections[0].footer.paragraphs[0].text = "same"
    return document


def _docx_variant_empty():
    return DocxDocument()


def _docx_variant_stray_run_fallback():
    document = DocxDocument()
    document.add_paragraph("one")
    _body_xml(document, "<w:r><w:t>stray run</w:t></w:r>")
    document.add_paragraph("two")
    return document


_DOCX_VARIANTS = [
    _docx_variant_plain,
    _docx_variant_leading_trailing_blanks,
    _docx_variant_nested_table,
    _docx_variant_table_last,
    _docx_variant_content_controls_and_textbox,
    _docx_variant_header_footer_duplicates,
    _docx_variant_empty,
    _docx_variant_stray_run_fallback,
]


@pytest.mark.asyncio
@pytest.mark.parametrize("build", _DOCX_VARIANTS, ids=lambda f: f.__name__)
async def test_docx_text_matches_legacy_flat_text(tmp_path: Path, build) -> None:
    """New text is byte-identical to the pre-blocks flat text and joins from the blocks."""

    document = build()
    buffer = BytesIO()
    document.save(buffer)
    path = _write_encrypted_file(tmp_path, "v.docx.enc", buffer.getvalue())

    result = await DocxIngester().ingest(path, mime_type="x", file_format="docx")

    assert result.text == _legacy_docx_text(DocxDocument(BytesIO(buffer.getvalue())))
    assert join_blocks(result.blocks) == result.text
    assert all(b.text is not None for b in result.blocks)


@pytest.mark.asyncio
async def test_docx_stray_body_run_falls_back_to_single_block(tmp_path: Path) -> None:
    """Body text without a paragraph terminator makes the split unreliable: one block, same text."""

    document = _docx_variant_stray_run_fallback()
    buffer = BytesIO()
    document.save(buffer)
    path = _write_encrypted_file(tmp_path, "stray.docx.enc", buffer.getvalue())

    result = await DocxIngester().ingest(path, mime_type="x", file_format="docx")

    assert [b.kind for b in result.blocks] == ["paragraph"]
    assert "stray run" in result.text


@pytest.mark.asyncio
async def test_docx_content_controls_textbox_and_nested_table_blocks(tmp_path: Path) -> None:
    """Content-control and text-box text survives into blocks; the fallback copy does not."""

    for build, expected in (
        (_docx_variant_content_controls_and_textbox, ["block control one", "inline control", "boxed"]),
        (_docx_variant_nested_table, ["n1", "n2"]),
    ):
        buffer = BytesIO()
        build().save(buffer)
        path = _write_encrypted_file(tmp_path, f"{build.__name__}.enc", buffer.getvalue())
        result = await DocxIngester().ingest(path, mime_type="x", file_format="docx")
        for needle in expected:
            assert needle in "\n".join(b.text for b in result.blocks)
        assert "textbox fallback" not in result.text


def _patch_pdf(monkeypatch, texts, fields=None, confidences=()):
    from septum_api.services.ingestion import pdf_ingester as pi

    monkeypatch.setattr(
        PdfIngester,
        "_extract_pages_with_optional_ocr",
        lambda self, doc: (list(texts), list(confidences)),
    )

    class _Field:
        def __init__(self, label, value):
            self.label, self.value = label, value

    class _Extractor:
        def extract_from_pdf(self, stream):
            return [_Field(*f) for f in (fields or [])], None

    monkeypatch.setattr(pi, "TableFieldExtractor", _Extractor)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "texts",
    [["a", "", "c"], ["", ""], ["only"], []],
    ids=["empty-middle-page", "all-empty", "single", "none"],
)
async def test_pdf_empty_pages_keep_legacy_text_and_page_numbers(
    tmp_path: Path, monkeypatch, texts
) -> None:
    """Empty pages stay as empty page blocks, so numbering and the legacy join are preserved."""

    _patch_pdf(monkeypatch, texts, confidences=[0.5, 0.7])
    path = _write_encrypted_file(tmp_path, "p.pdf.enc", _create_sample_pdf_bytes("x"))

    result = await PdfIngester().ingest(path, mime_type="application/pdf", file_format="pdf")

    assert result.text == "\n\n".join(texts)
    assert join_blocks(result.blocks) == result.text
    assert [b.page for b in result.blocks] == list(range(1, len(texts) + 1))
    assert result.confidence == pytest.approx(0.6)


@pytest.mark.asyncio
async def test_pdf_fields_block_precedes_pages_and_matches_legacy_text(
    tmp_path: Path, monkeypatch
) -> None:
    """The fields block is first, page-less, and the text equals the legacy fields + pages layout."""

    _patch_pdf(monkeypatch, ["page one", "page two"], fields=[("Name", "X"), ("Id", "7")])
    path = _write_encrypted_file(tmp_path, "f.pdf.enc", _create_sample_pdf_bytes("x"))

    result = await PdfIngester().ingest(path, mime_type="application/pdf", file_format="pdf")

    legacy = "=== Extracted Fields ===\n\nName : X\nId : 7" + "\n\n" + "page one\n\npage two"
    assert result.text == legacy
    assert join_blocks(result.blocks) == result.text
    assert [(b.kind, b.page) for b in result.blocks] == [("fields", None), ("page", 1), ("page", 2)]
    assert result.metadata["extracted_fields_count"] == 2


@pytest.mark.asyncio
async def test_pdf_fields_with_cid_garbage_are_dropped(tmp_path: Path, monkeypatch) -> None:
    """Fields containing unmapped glyph markers are not added, and no fields block appears."""

    _patch_pdf(monkeypatch, ["p"], fields=[("(cid:12)", "v")])
    path = _write_encrypted_file(tmp_path, "c.pdf.enc", _create_sample_pdf_bytes("x"))

    result = await PdfIngester().ingest(path, mime_type="application/pdf", file_format="pdf")

    assert result.text == "p"
    assert [b.kind for b in result.blocks] == ["page"]


@pytest.mark.asyncio
async def test_pdf_fields_with_only_empty_pages(tmp_path: Path, monkeypatch) -> None:
    """Fields plus a single empty page still join to the legacy text."""

    _patch_pdf(monkeypatch, [""], fields=[("K", "V")])
    path = _write_encrypted_file(tmp_path, "e.pdf.enc", _create_sample_pdf_bytes("x"))

    result = await PdfIngester().ingest(path, mime_type="application/pdf", file_format="pdf")

    assert result.text == "=== Extracted Fields ===\n\nK : V\n\n"
    assert join_blocks(result.blocks) == result.text


@pytest.mark.asyncio
async def test_pdf_real_file_text_matches_legacy_join(tmp_path: Path) -> None:
    """On a real multi-page PDF (with a blank page) the text equals the legacy page join."""

    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "alpha")
    doc.new_page()
    doc.new_page().insert_text((72, 72), "gamma")
    data = doc.tobytes()
    doc.close()
    path = _write_encrypted_file(tmp_path, "r.pdf.enc", data)

    result = await PdfIngester().ingest(path, mime_type="application/pdf", file_format="pdf")

    pages = [b.text for b in result.blocks if b.kind == "page"]
    assert len(pages) == 3
    body = "\n\n".join(pages)
    assert result.text.endswith(body)
    assert join_blocks(result.blocks) == result.text


def _sheet_workbook(tmp_path: Path, name: str) -> Path:
    wb = Workbook()
    first = wb.active
    first.title = "Data"
    first.append([None, None, None])
    first.append([None, "h1", "h2"])
    first.append([None, "x", None])
    first.append([])
    first.append([None, "line\nbreak", "z"])
    wb.create_sheet("Blank")
    wb.create_sheet("Solo").append(["only"])
    buffer = BytesIO()
    wb.save(buffer)
    return _write_encrypted_file(tmp_path, name, buffer.getvalue())


@pytest.mark.asyncio
async def test_xlsx_text_matches_legacy_layout_with_blank_and_offset_sheets(
    tmp_path: Path,
) -> None:
    """Blank sheets add no block, sheets join by a blank line, rows by a newline."""

    from septum_api.models.spreadsheet_schema import generated_technical_label as label

    result = await XlsxIngester().ingest(
        _sheet_workbook(tmp_path, "s.xlsx.enc"), mime_type="x", file_format="xlsx"
    )

    l0, l1 = label(0), label(1)
    legacy = (
        f"# Sheet: Data — columns: {l0} | {l1}\n{l0}\t{l1}\nx\nline\nbreak\tz"
        f"\n\n# Sheet: Solo — columns: {l0}\n{l0}"
    )
    assert result.text == legacy
    assert join_blocks(result.blocks) == result.text
    assert [b.sheet for b in result.blocks if b.kind == "sheet_header"] == ["Data", "Solo"]
    assert "Blank" not in {b.sheet for b in result.blocks}


def test_audio_segment_blocks_property_joins_to_text() -> None:
    """For randomized transcripts (repeats, odd whitespace, dropped segments) the join is exact."""

    import random

    rng = random.Random(1234)
    words = ["yes", "no", "the", "the", "ok", "ünï", "a b"]
    for _ in range(300):
        segments = []
        for index in range(rng.randint(0, 6)):
            segment_text = " " * rng.randint(0, 2) + rng.choice(words) + " " * rng.randint(0, 2)
            segments.append({"text": segment_text, "start": float(index), "end": index + 0.9})
        glue = rng.choice([" ", "", "\n", "  "])
        text = glue.join(s["text"].strip() for s in segments)
        if rng.random() < 0.2:
            text = " " + text
        if rng.random() < 0.2 and segments:
            text = text.replace(segments[-1]["text"].strip(), "", 1)
        blocks = _segment_blocks(text, segments)
        assert join_blocks(blocks) == text
