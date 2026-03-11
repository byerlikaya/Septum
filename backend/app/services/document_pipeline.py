from __future__ import annotations

import asyncio
from typing import Any, List, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.document import Chunk as DocumentChunk, Document
from ..models.regulation import RegulationRuleset
from ..models.settings import AppSettings
from .anonymization_map import AnonymizationMap
from .chunking_strategy import (
    Chunk as SemanticChunk,
    SlidingWindowChunker,
    StructuredDocumentChunker,
)
from .document_anon_store import set_document_map
from .policy_composer import PolicyComposer
from .sanitizer import PIISanitizer
from .text_normalizer import TextNormalizer
from .vector_store import VectorStore


class DocumentPipeline:
    """Orchestrates sanitization, chunking, and indexing for ingested documents."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    async def run(
        self,
        db: AsyncSession,
        document: Document,
        file_format: str,
        ingested_text: str,
        ingestion_confidence: float | None,
    ) -> None:
        """Sanitize text, create chunks, and index them in the vector store."""
        detected_language = document.detected_language
        anon_map = AnonymizationMap(document_id=document.id, language=detected_language)

        policy = await self._compose_policy(db)
        sanitizer = PIISanitizer(settings=self._settings, policy=policy)

        sanitize_result = await asyncio.to_thread(
            sanitizer.sanitize,
            ingested_text,
            detected_language,
            anon_map,
        )

        normalizer = TextNormalizer()
        normalized_text = await normalizer.normalize(
            db,
            sanitize_result.sanitized_text,
        )

        document.transcription_text = normalized_text
        document.ocr_confidence = ingestion_confidence

        semantic_chunks = await self._build_chunks(file_format, normalized_text)
        chunks = await self._persist_chunks(db, document.id, semantic_chunks)

        document.chunk_count = len(chunks)
        document.entity_count = sanitize_result.entity_count
        document.ingestion_status = "completed"
        document.ingestion_error = None
        await db.commit()
        await db.refresh(document)

        set_document_map(document.id, anon_map)

        if chunks:
            await asyncio.to_thread(
                self._index_chunks,
                document.id,
                chunks,
            )

    async def _compose_policy(self, db: AsyncSession) -> Any:
        composer = PolicyComposer()
        return await composer.compose(db)

    async def _build_chunks(
        self,
        file_format: str,
        normalized_text: str,
    ) -> Sequence[SemanticChunk]:
        if file_format in {"pdf", "docx"}:
            chunker = StructuredDocumentChunker(
                max_chunk_size=max(self._settings.pdf_chunk_size, 800)
            )
        else:
            chunker = SlidingWindowChunker(
                chunk_size=max(self._settings.chunk_size, 1),
                overlap=max(
                    min(self._settings.chunk_overlap, self._settings.chunk_size - 1),
                    0,
                ),
            )

        semantic_chunks = await asyncio.to_thread(chunker.chunk, normalized_text)
        return self._merge_title_chunks(semantic_chunks)

    async def _persist_chunks(
        self,
        db: AsyncSession,
        document_id: int,
        semantic_chunks: Sequence[SemanticChunk],
    ) -> List[DocumentChunk]:
        chunks: List[DocumentChunk] = []
        for semantic_chunk in semantic_chunks:
            chunk = DocumentChunk(
                document_id=document_id,
                index=semantic_chunk.index,
                sanitized_text=semantic_chunk.text,
                char_count=semantic_chunk.char_count,
                source_page=semantic_chunk.source_page,
                source_slide=None,
                source_sheet=None,
                source_timestamp_start=None,
                source_timestamp_end=None,
                section_title=semantic_chunk.section_title,
            )
            db.add(chunk)
            chunks.append(chunk)

        await db.commit()
        for chunk in chunks:
            await db.refresh(chunk)
        return chunks

    @staticmethod
    def _merge_title_chunks(
        semantic_chunks: Sequence[SemanticChunk],
    ) -> List[SemanticChunk]:
        merged_chunks: List[SemanticChunk] = []
        i = 0
        length = len(semantic_chunks)
        while i < length:
            current = semantic_chunks[i]

            if (
                current.char_count < 50
                and current.section_title
                and i + 1 < length
            ):
                next_chunk = semantic_chunks[i + 1]
                merged_text = f"{current.text.strip()}\n{next_chunk.text}"
                merged_title = current.section_title or next_chunk.section_title

                merged_chunk = SemanticChunk(
                    text=merged_text,
                    index=current.index,
                    source_page=current.source_page or next_chunk.source_page,
                    section_title=merged_title,
                    char_count=len(merged_text),
                )
                merged_chunks.append(merged_chunk)
                i += 2
            else:
                merged_chunks.append(current)
                i += 1
        return merged_chunks

    @staticmethod
    def _index_chunks(document_id: int, chunks: Sequence[DocumentChunk]) -> None:
        vector_store = VectorStore()
        vector_store.index_document(
            document_id,
            [c.id for c in chunks],
            [c.sanitized_text for c in chunks],
        )

