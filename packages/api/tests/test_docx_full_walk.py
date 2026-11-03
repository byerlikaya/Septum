"""DOCX ingestion reads every text-bearing part, not only body paragraphs.

Tables (nested too), headers and footers, content controls, text boxes,
tracked insertions and comments all carry document text. Deleted text and
field codes are not content, and a text box stored twice for
compatibility (``mc:Choice`` and ``mc:Fallback``) is read once.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from docx import Document as DocxDocument  # type: ignore[import]
from docx.oxml import parse_xml  # type: ignore[import]

from septum_api.services.ingestion.docx_ingester import DocxIngester
from septum_api.utils.crypto import encrypt

_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:v="urn:schemas-microsoft-com:vml"'
)
_BOX = (
    "<w:pict><v:shape><v:textbox><w:txbxContent>"
    "<w:p><w:r><w:t>Box F</w:t></w:r></w:p>"
    "</w:txbxContent></v:textbox></v:shape></w:pict>"
)


def _body_xml(document, xml: str) -> None:
    body = document.element.body
    body.insert(len(body) - 1, parse_xml(xml))


def _build() -> bytes:
    document = DocxDocument()
    section = document.sections[0]
    section.header.paragraphs[0].text = "Header Acme"
    section.footer.paragraphs[0].text = "Footer 42"

    intro = document.add_paragraph("Intro paragraph")
    document.add_comment(intro.runs, text="Comment G", author="Reviewer")

    table = document.add_table(rows=2, cols=1)
    table.cell(0, 0).text = "Cell A"
    nested = table.cell(1, 0).add_table(rows=1, cols=1)
    nested.cell(0, 0).text = "Nested B"

    _body_xml(document, f"<w:sdt {_NS}><w:sdtContent><w:p><w:r><w:t>Control C</w:t></w:r></w:p></w:sdtContent></w:sdt>")
    _body_xml(
        document,
        f"<w:p {_NS}><w:r><w:t xml:space=\"preserve\">Kept </w:t></w:r>"
        "<w:ins w:id=\"1\" w:author=\"a\"><w:r><w:t>Inserted D</w:t></w:r></w:ins>"
        "<w:del w:id=\"2\" w:author=\"a\"><w:r><w:delText>Deleted E</w:delText></w:r></w:del>"
        "<w:r><w:instrText>PAGE \\* MERGEFORMAT</w:instrText></w:r></w:p>",
    )
    _body_xml(
        document,
        f"<w:p {_NS}><w:r><mc:AlternateContent>"
        f"<mc:Choice Requires=\"wps\">{_BOX}</mc:Choice>"
        f"<mc:Fallback>{_BOX}</mc:Fallback>"
        "</mc:AlternateContent></w:r></w:p>",
    )
    _body_xml(document, f"<w:p {_NS}><w:r><w:t>Tab</w:t><w:tab/><w:t>Stop</w:t><w:br/><w:t>Next line</w:t></w:r></w:p>")

    second = document.add_section()
    second.header.is_linked_to_previous = False
    second.header.paragraphs[0].text = "Header Acme"

    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


async def _ingest(tmp_path: Path, data: bytes):
    path = tmp_path / "doc.docx.enc"
    path.write_bytes(encrypt(data))
    return await DocxIngester().ingest(
        path,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        file_format="docx",
    )


@pytest.fixture
async def text(tmp_path: Path) -> str:
    return (await _ingest(tmp_path, _build())).text


@pytest.mark.parametrize(
    "fragment",
    [
        "Intro paragraph",
        "Cell A",
        "Nested B",
        "Control C",
        "Kept Inserted D",
        "Header Acme",
        "Footer 42",
        "Comment G",
        "Tab\tStop\nNext line",
    ],
)
async def test_every_text_part_is_read(text: str, fragment: str) -> None:
    assert fragment in text


@pytest.mark.parametrize("fragment", ["Deleted E", "PAGE", "MERGEFORMAT"])
async def test_deleted_text_and_field_codes_are_skipped(text: str, fragment: str) -> None:
    assert fragment not in text


async def test_text_box_and_repeated_header_are_read_once(text: str) -> None:
    assert text.count("Box F") == 1
    assert text.count("Header Acme") == 1


async def test_body_keeps_document_order(text: str) -> None:
    order = ["Intro paragraph", "Cell A", "Nested B", "Control C", "Kept Inserted D", "Box F", "Tab"]
    positions = [text.index(fragment) for fragment in order]
    assert positions == sorted(positions)
    assert text.index("Tab") < text.index("Header Acme") < text.index("Comment G")


async def test_simple_document_keeps_its_text_and_paragraph_count(tmp_path: Path) -> None:
    document = DocxDocument()
    document.add_paragraph("Hello from DOCX ingester")
    buffer = BytesIO()
    document.save(buffer)
    result = await _ingest(tmp_path, buffer.getvalue())
    assert result.text == "Hello from DOCX ingester"
    assert result.metadata["paragraph_count"] == 1
