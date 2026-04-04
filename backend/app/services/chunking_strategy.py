from __future__ import annotations

"""Semantic chunking strategies for different document types.

This module provides intelligent chunking that preserves document structure
(headings, sections, clauses, paragraphs) rather than blindly using sliding
windows. Combines structural detection with embedding-based semantic splitting
for optimal chunking of legal/contract documents.

Strategy:
1. Structural phase: detect numbered sections/clauses
2. Semantic phase: split large sections by semantic similarity
3. Field extraction: preserve key-value pairs as separate chunks
"""

import re
from dataclasses import dataclass
from typing import List, Optional

from langchain_experimental.text_splitter import (
    SemanticChunker as LangChainSemanticChunker,
)


@dataclass
class Chunk:
    """Represents a semantic chunk of text with optional metadata."""

    text: str
    index: int
    source_page: Optional[int] = None
    section_title: Optional[str] = None
    char_count: int = 0

    def __post_init__(self) -> None:
        if self.char_count == 0:
            self.char_count = len(self.text)


class SemanticChunker:
    """Base class for semantic chunking strategies."""

    def __init__(self, max_chunk_size: int = 1200, min_chunk_size: int = 100) -> None:
        self.max_chunk_size = max_chunk_size
        self.min_chunk_size = min_chunk_size

    def chunk(self, text: str) -> List[Chunk]:
        """Split text into semantic chunks."""
        raise NotImplementedError


class StructuredDocumentChunker(SemanticChunker):
    """Enhanced chunker for structured legal/business documents.

    Hybrid approach:
    1. Structural phase: detect numbered sections/clauses
    2. Semantic phase: split large sections using embeddings
    3. Preserves structure while respecting semantic coherence

    Uses LangChain's SemanticChunker with gradient threshold for
    embedding-based splitting of large sections.
    """

    # Patterns for section/clause numbers (PURELY structural, NO keywords)
    _NUMBERED_SECTION_PATTERNS = [
        r"^\s*\d+\.\s+[A-Z\u00C0-\u024F\u0400-\u04FF]",  # "1. " followed by any uppercase letter (Latin, extended Latin, Cyrillic)
        r"^\s*\d+\.\d+\.\s*",  # "2.1. " or "2.1."
        r"^\s*\d+\.\d+\.\d+\.\s*",  # "2.1.1. " or "2.1.1."
        r"^\s*\d+\.\d+\.\d+\.\d+\.\s*",  # "2.1.1.1. "
        r"^\s*\w+\s+\d+[:\.]?\s*$",  # "Word 5:" or "Word 5." (generic keyword + number pattern)
        r"^\s*\([a-zA-Z]\)\s*",  # "(a) " or "(A) " sub-clause
        r"^\s*\(\d+\)\s*",  # "(1) " sub-clause
        r"^\s*[A-Z]\.\s+",  # "A. " enumeration
        r"^\s*[IVX]+\.\s+",  # Roman numerals: "I. ", "II. ", "III. "
    ]

    def __init__(self, max_chunk_size: int = 1200, min_chunk_size: int = 100) -> None:
        super().__init__(max_chunk_size=max_chunk_size, min_chunk_size=min_chunk_size)
        self._semantic_splitter: Optional[LangChainSemanticChunker] = None

    def _get_semantic_splitter(self) -> LangChainSemanticChunker:
        """Lazy-load the LangChain SemanticChunker with embeddings."""
        if self._semantic_splitter is None:
            from langchain_core.embeddings import Embeddings

            from .vector_store import get_shared_sentence_transformer

            embeddings_model = get_shared_sentence_transformer()

            class _STEmbeddings(Embeddings):
                def __init__(self, model: object) -> None:
                    self.model = model

                def embed_documents(self, texts: List[str]) -> List[List[float]]:
                    return self.model.encode(texts, convert_to_numpy=True).tolist()

                def embed_query(self, text: str) -> List[float]:
                    return self.model.encode([text], convert_to_numpy=True)[0].tolist()

            self._semantic_splitter = LangChainSemanticChunker(
                embeddings=_STEmbeddings(embeddings_model),
                breakpoint_threshold_type="gradient",
            )

        return self._semantic_splitter

    def chunk(self, text: str) -> List[Chunk]:
        """Chunk by detecting numbered sections and using semantic splitting."""
        if not text or len(text) < self.min_chunk_size:
            return [Chunk(text=text, index=0)]

        lines = text.split("\n")
        sections = self._identify_sections(lines)

        if not sections:
            return self._fallback_semantic_chunking(text)

        chunks: List[Chunk] = []
        for idx, section in enumerate(sections):
            section_text = section["text"]
            section_title = section.get("title")

            if len(section_text) <= self.max_chunk_size:
                chunks.append(
                    Chunk(
                        text=section_text,
                        index=len(chunks),
                        section_title=section_title,
                    )
                )
            else:
                # Use semantic splitting for large sections
                sub_chunks = self._split_large_section_semantic(section_text, section_title)
                for sub_chunk in sub_chunks:
                    sub_chunk.index = len(chunks)
                    chunks.append(sub_chunk)

        return chunks

    def _identify_sections(self, lines: List[str]) -> List[dict]:
        """Identify sections by numbered headings and all-caps titles."""
        sections: List[dict] = []
        current_section_lines: List[str] = []
        current_title: Optional[str] = None

        for line in lines:
            stripped = line.strip()

            if not stripped:
                if current_section_lines:
                    current_section_lines.append(line)
                continue

            is_section_start = self._is_section_start(stripped)
            is_heading = self._is_heading(stripped)

            if is_section_start or is_heading:
                if current_section_lines:
                    section_text = "\n".join(current_section_lines).strip()
                    sections.append(
                        {
                            "text": section_text,
                            "title": current_title,
                        }
                    )
                current_section_lines = [line]
                current_title = stripped[:100] if len(stripped) < 200 else None
            else:
                current_section_lines.append(line)

        if current_section_lines:
            sections.append(
                {
                    "text": "\n".join(current_section_lines).strip(),
                    "title": current_title,
                }
            )

        return sections

    def _is_section_start(self, line: str) -> bool:
        """Check if line starts with a section/clause number."""
        for pattern in self._NUMBERED_SECTION_PATTERNS:
            if re.match(pattern, line, re.IGNORECASE):
                return True
        return False

    def _is_heading(self, line: str) -> bool:
        """Check if line is a heading using ONLY structural features.

        Structural features (language-agnostic):
        - Ends with colon (common heading pattern)
        - High uppercase ratio (>70%)
        - Appropriate length (5-150 chars)
        - No language-specific keywords checked
        """
        if len(line) < 5 or len(line) > 150:
            return False

        # Colon suffix strongly indicates heading
        if line.endswith(":"):
            return True

        # High uppercase ratio indicates heading (works across all scripts that have case)
        alpha_chars = [c for c in line if c.isalpha()]
        if not alpha_chars:
            return False

        upper_ratio = sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars)
        return upper_ratio > 0.7

    def _split_large_section_semantic(
        self, text: str, section_title: Optional[str]
    ) -> List[Chunk]:
        """Split a large section using semantic similarity (gradient-based)."""
        try:
            splitter = self._get_semantic_splitter()
            langchain_docs = splitter.split_text(text)

            chunks: List[Chunk] = []
            for doc_text in langchain_docs:
                if len(doc_text.strip()) >= self.min_chunk_size:
                    chunks.append(
                        Chunk(
                            text=doc_text,
                            index=0,
                            section_title=section_title,
                        )
                    )

            return chunks if chunks else [Chunk(text=text, index=0, section_title=section_title)]
        except Exception:
            # Fallback to paragraph-based splitting if semantic fails
            return self._split_large_section_paragraph(text, section_title)

    def _split_large_section_paragraph(
        self, text: str, section_title: Optional[str]
    ) -> List[Chunk]:
        """Fallback: split a large section by paragraphs."""
        paragraphs = re.split(r"\n\s*\n", text)
        chunks: List[Chunk] = []
        current_text = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            if len(current_text) + len(para) + 2 <= self.max_chunk_size:
                if current_text:
                    current_text += "\n\n" + para
                else:
                    current_text = para
            else:
                if current_text:
                    chunks.append(
                        Chunk(
                            text=current_text,
                            index=0,
                            section_title=section_title,
                        )
                    )
                current_text = para

        if current_text:
            chunks.append(
                Chunk(
                    text=current_text,
                    index=0,
                    section_title=section_title,
                )
            )

        return chunks

    def _fallback_semantic_chunking(self, text: str) -> List[Chunk]:
        """Fallback: use semantic chunking when no structure is detected."""
        try:
            splitter = self._get_semantic_splitter()
            langchain_docs = splitter.split_text(text)

            chunks: List[Chunk] = []
            for idx, doc_text in enumerate(langchain_docs):
                if len(doc_text.strip()) >= self.min_chunk_size:
                    chunks.append(Chunk(text=doc_text, index=idx))

            return chunks if chunks else [Chunk(text=text, index=0)]
        except Exception:
            # Final fallback to paragraph-based
            return self._fallback_paragraph_chunking(text)

    def _fallback_paragraph_chunking(self, text: str) -> List[Chunk]:
        """Final fallback: chunk by paragraphs when semantic chunking fails."""
        paragraphs = re.split(r"\n\s*\n", text)
        chunks: List[Chunk] = []
        current_text = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            if len(current_text) + len(para) + 2 <= self.max_chunk_size:
                if current_text:
                    current_text += "\n\n" + para
                else:
                    current_text = para
            else:
                if current_text:
                    chunks.append(Chunk(text=current_text, index=len(chunks)))
                current_text = para

        if current_text:
            chunks.append(Chunk(text=current_text, index=len(chunks)))

        return chunks if chunks else [Chunk(text=text, index=0)]


class SlidingWindowChunker(SemanticChunker):
    """Simple sliding window chunker for unstructured text."""

    def __init__(
        self, chunk_size: int = 800, overlap: int = 200, max_chunk_size: int = 1200
    ) -> None:
        super().__init__(max_chunk_size=max_chunk_size)
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk(self, text: str) -> List[Chunk]:
        """Chunk by sliding window with overlap."""
        if not text:
            return []

        chunks: List[Chunk] = []
        text_len = len(text)
        index = 0
        chunk_index = 0

        while index < text_len:
            end = min(index + self.chunk_size, text_len)
            segment = text[index:end]
            chunks.append(Chunk(text=segment, index=chunk_index))

            if end >= text_len:
                break

            index = end - self.overlap
            chunk_index += 1

        return chunks
