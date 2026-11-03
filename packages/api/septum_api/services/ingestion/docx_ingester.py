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
from typing import Any, Dict, Iterator, List

from docx import Document as DocxDocument  # type: ignore[import]
from docx.oxml import parse_xml  # type: ignore[import]

from ...utils.crypto import decrypt
from .base import BaseIngester, IngestionResult

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


def _document_text(document: Any) -> str:
    """Body text, then each distinct related part's text, separated by blank lines."""
    sections = [_part_text(document.element.body)]
    for element in _related_part_elements(document):
        text = _part_text(element)
        if text and text not in sections:
            sections.append(text)
    return "\n\n".join(section for section in sections if section)


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

        full_text = _document_text(document)

        metadata: Dict[str, Any] = {
            "paragraph_count": len(document.paragraphs),
            "mime_type": mime_type,
            "file_format": file_format,
        }

        return IngestionResult(text=full_text, metadata=metadata)

