from __future__ import annotations

"""DOCX document ingester using python-docx.

This ingester is responsible for:
    - Reading the encrypted DOCX bytes from disk.
    - Decrypting them in memory using the shared AES-256-GCM utilities.
    - Extracting the text of every text-bearing part in document order:
      the body (tables at any depth, content controls, text boxes, tracked
      insertions), then headers and footers, footnotes, endnotes and
      comments. Deleted text and field codes are not content and are
      skipped; a text box stored twice for compatibility (``mc:Choice``
      and ``mc:Fallback``) is read once.
    - Returning an :class:`IngestionResult` with the concatenated text and
      lightweight, non-PII metadata (e.g., paragraph count).
"""

import asyncio
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterator, List, Set

from docx import Document as DocxDocument  # type: ignore[import]
from docx.oxml import parse_xml  # type: ignore[import]

from ...utils.crypto import decrypt
from .base import BaseIngester, IngestionResult, TextBlock, join_blocks

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
# Subtrees that hold no document text: deleted runs, field instructions,
# and the compatibility copy of content already read from ``mc:Choice``.
_SKIPPED = frozenset({f"{_W}delText", f"{_W}instrText", f"{_MC}Fallback"})
_BREAKS = frozenset({f"{_W}br", f"{_W}cr"})
# Related parts read after the body, in this order (relationship type suffix).
_PART_ORDER = ("/header", "/footer", "/footnotes", "/endnotes", "/comments")


def _walk(element: Any, out: List[str]) -> None:
    """Append the text of ``element`` in document order to ``out``."""
    tag = element.tag
    if not isinstance(tag, str) or tag in _SKIPPED:
        return
    if tag == f"{_W}t":
        out.append(element.text or "")
        return
    if tag == f"{_W}tab":
        out.append("\t")
        return
    if tag in _BREAKS:
        out.append("\n")
        return
    for child in element:
        _walk(child, out)
    if tag == f"{_W}p":
        out.append("\n")


def _part_text(element: Any) -> str:
    out: List[str] = []
    _walk(element, out)
    return "".join(out).strip()


def _related_part_elements(document: Any) -> Iterator[Any]:
    """Root elements of the header, footer, note and comment parts, in a stable order."""
    related = [
        (rank, str(rel.target_part.partname), rel.target_part)
        for rel in document.part.rels.values()
        if not rel.is_external
        for rank, suffix in enumerate(_PART_ORDER)
        if rel.reltype.endswith(suffix)
    ]
    for _, _, part in sorted(related, key=lambda item: item[:2]):
        element = getattr(part, "element", None)
        yield element if element is not None else parse_xml(part.blob)


def _heading_style_ids(document: Any) -> Set[str]:
    """Ids of paragraph styles that mark a heading.

    A style is a heading when it declares an outline level or is one of the
    built-in heading styles (``Title`` and ``Heading N``).
    """
    ids: Set[str] = set()
    for style in document.styles:
        element = style.element
        if element.find(f"{_W}pPr/{_W}outlineLvl") is not None:
            ids.add(style.style_id)
            continue
        name = (style.name or "").lower()
        if name == "title" or name.startswith("heading "):
            ids.add(style.style_id)
    return ids


def _is_heading(paragraph: Any, heading_styles: Set[str]) -> bool:
    """Whether a body paragraph is a heading by its own outline level or its style."""
    if paragraph.find(f"{_W}pPr/{_W}outlineLvl") is not None:
        return True
    style = paragraph.find(f"{_W}pPr/{_W}pStyle")
    return style is not None and style.get(f"{_W}val") in heading_styles


def _body_blocks(document: Any) -> List[TextBlock]:
    """One block per top-level body element, in document order.

    Paragraphs are ``heading`` or ``paragraph`` blocks, tables are ``table``
    blocks. Leading and trailing blank elements are dropped, as the flat body
    text is stripped. An element whose text has no paragraph terminator makes
    the split unreliable, so no blocks are returned.
    """
    heading_styles = _heading_style_ids(document)
    blocks: List[TextBlock] = []
    for child in document.element.body:
        out: List[str] = []
        _walk(child, out)
        text = "".join(out)
        if not text:
            continue
        if not text.endswith("\n"):
            return []
        if child.tag == f"{_W}tbl":
            kind = "table"
        elif child.tag == f"{_W}p" and _is_heading(child, heading_styles):
            kind = "heading"
        else:
            kind = "paragraph"
        blocks.append(TextBlock(text=text[:-1], kind=kind))
    while blocks and not blocks[0].text.strip():
        blocks.pop(0)
    while blocks and not blocks[-1].text.strip():
        blocks.pop()
    if blocks:
        blocks[0].text = blocks[0].text.lstrip()
        blocks[-1].text = blocks[-1].text.rstrip()
    return blocks


def _document_blocks(document: Any) -> List[TextBlock]:
    """Body blocks, then each distinct related part's text as a ``part`` block."""
    body = _part_text(document.element.body)
    blocks = _body_blocks(document)
    if join_blocks(blocks) != body:
        blocks = [TextBlock(text=body, kind="paragraph")] if body else []
    seen = [body]
    for element in _related_part_elements(document):
        text = _part_text(element)
        if text and text not in seen:
            seen.append(text)
            blocks.append(TextBlock(text=text, kind="part"))
    return blocks


class DocxIngester(BaseIngester):
    """Ingests encrypted DOCX documents and extracts their textual content."""

    async def ingest(
        self,
        file_path: Path,
        *,
        mime_type: str,
        file_format: str,
    ) -> IngestionResult:
        """Ingest the DOCX at ``file_path`` and return extracted content."""

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
        """Synchronous part of DOCX ingestion, run in a worker thread."""

        encrypted_bytes = file_path.read_bytes()
        docx_bytes = decrypt(encrypted_bytes)

        document = DocxDocument(BytesIO(docx_bytes))

        blocks = _document_blocks(document)
        full_text = join_blocks(blocks)

        metadata: Dict[str, Any] = {
            "paragraph_count": len(document.paragraphs),
            "mime_type": mime_type,
            "file_format": file_format,
        }

        return IngestionResult(text=full_text, metadata=metadata, blocks=blocks)

