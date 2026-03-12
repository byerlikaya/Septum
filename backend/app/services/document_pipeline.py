from __future__ import annotations

import asyncio
from typing import Any, List, Sequence, Tuple

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
from .bm25_retriever import BM25Retriever
from .document_anon_store import set_document_map
from .ner_model_registry import NERModelRegistry
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
        """Create chunks, build anonymization map, and index them in the vector store.

        Chunks are persisted using normalized raw text (no placeholders) so that
        document views and chunk search operate on the original content. PII
        detection is still executed per chunk in order to populate the
        anonymization map and entity counters, but the sanitized strings are not
        stored; masking happens only when chunks are sent to the LLM.
        """
        detected_language = document.detected_language
        anon_map = AnonymizationMap(document_id=document.id, language=detected_language)

        policy = await self._compose_policy(db)
        overrides = getattr(self._settings, "ner_model_overrides", None) or {}
        ner_registry = NERModelRegistry(_overrides=dict(overrides))
        sanitizer = PIISanitizer(
            settings=self._settings,
            policy=policy,
            ner_registry=ner_registry,
            enable_ollama_layer=False,
        )

        semantic_chunks = await self._build_chunks(file_format, ingested_text)

        normalizer = TextNormalizer()

        stored_chunks: List[SemanticChunk] = []
        raw_texts_for_index: List[str] = []
        total_entities = 0

        for semantic_chunk in semantic_chunks:
            raw_text = semantic_chunk.text
            normalized_raw = await normalizer.normalize(db, raw_text)
            raw_texts_for_index.append(normalized_raw)
            sanitize_result = await asyncio.to_thread(
                sanitizer.sanitize,
                normalized_raw,
                detected_language,
                anon_map,
            )
            total_entities += sanitize_result.entity_count
            stored_chunks.append(
                SemanticChunk(
                    text=normalized_raw,
                    index=semantic_chunk.index,
                    source_page=semantic_chunk.source_page,
                    section_title=semantic_chunk.section_title,
                    char_count=len(normalized_raw),
                )
            )

        document.transcription_text = "\n\n".join(chunk.text for chunk in stored_chunks)
        document.ocr_confidence = ingestion_confidence

        chunks = await self._persist_chunks(db, document.id, stored_chunks)

        document.chunk_count = len(chunks)
        document.entity_count = total_entities
        document.ingestion_status = "completed"
        document.ingestion_error = None
        await db.commit()
        await db.refresh(document)

        set_document_map(document.id, anon_map)

        if chunks and raw_texts_for_index:
            await asyncio.to_thread(
                self._index_chunks,
                document.id,
                chunks,
                raw_texts_for_index,
            )
            await asyncio.to_thread(
                self._index_chunks_bm25,
                document.id,
                chunks,
                raw_texts_for_index,
            )

    async def _compose_policy(self, db: AsyncSession) -> Any:
        composer = PolicyComposer()
        return await composer.compose(db)

    async def _build_chunks(
        self,
        file_format: str,
        raw_text: str,
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

        semantic_chunks = await asyncio.to_thread(chunker.chunk, raw_text)
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
    def _index_chunks(
        document_id: int,
        chunks: Sequence[DocumentChunk],
        raw_texts: Sequence[str],
    ) -> None:
        if len(chunks) != len(raw_texts):
            ids = [c.id for c in chunks]
            raw_len = len(raw_texts)
            from time import time as _time
            import json as _json

            # #region agent log
            try:
                with open(
                    "/Users/barisyerlikaya/Projects/Septum/.cursor/debug-314f97.log",
                    "a",
                    encoding="utf-8",
                ) as f:
                    f.write(
                        _json.dumps(
                            {
                                "sessionId": "314f97",
                                "runId": "upload",
                                "hypothesisId": "U2",
                                "location": "services/document_pipeline.py:_index_chunks",
                                "message": "chunk_raw_length_mismatch",
                                "data": {
                                    "document_id": document_id,
                                    "chunk_ids": ids,
                                    "raw_text_count": raw_len,
                                },
                                "timestamp": _time(),
                            }
                        )
                        + "\n"
                    )
            except Exception:
                pass
            # #endregion
            return

        vector_store = VectorStore()
        vector_store.index_document(
            document_id,
            [c.id for c in chunks],
            list(raw_texts),
        )

    @staticmethod
    def _index_chunks_bm25(
        document_id: int,
        chunks: Sequence[DocumentChunk],
        raw_texts: Sequence[str],
    ) -> None:
        """Build BM25 index for keyword-based retrieval."""
        if len(chunks) != len(raw_texts):
            return

        bm25_retriever = BM25Retriever()
        bm25_retriever.index_document(
            document_id,
            [c.id for c in chunks],
            list(raw_texts),
        )

