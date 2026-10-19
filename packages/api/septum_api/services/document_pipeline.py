from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import ContentFreeError
from ..models.document import Chunk as DocumentChunk
from ..models.document import Document
from ..models.entity_detection import EntityDetection
from ..models.settings import AppSettings
from .anonymization_map import AnonymizationMap
from .audit_logger import log_pii_detected
from .bm25_retriever import BM25Retriever
from .chunking_strategy import (
    Chunk as SemanticChunk,
)
from .chunking_strategy import (
    SlidingWindowChunker,
    StructuredDocumentChunker,
)
from .document_anon_store import document_map_lock, pop_document_map, set_document_map
from .entity_index_service import (
    recompute_relationships_for_document,
    replace_index_for_document,
)
from .sanitizer import ResolvedSpan
from .sanitizer_factory import create_sanitizer
from .text_normalizer import TextNormalizer
from .vector_store import VectorStore


class DocumentDeletedDuringIngestionError(RuntimeError):
    """The document row disappeared while its artifacts were being written."""


_ARTIFACT_TIER_LABELS = {
    "vector_index": "FAISS index",
    "bm25_index": "BM25 index",
    "anonymization_map": "anonymization map",
}


class ArtifactCleanupError(ContentFreeError, RuntimeError):
    """Erasure failed; the message names only the document id, tier and error class."""


class ArtifactErasureError(RuntimeError):
    """A stored copy of a document could not be erased; ``tier`` names it."""

    def __init__(self, tier: str, error: BaseException) -> None:
        super().__init__(tier)
        self.tier = tier
        self.error = error

    @property
    def label(self) -> str:
        return _ARTIFACT_TIER_LABELS.get(self.tier, self.tier)


async def erase_document_artifacts(
    document_id: int, *, source_path: Optional[str] = None
) -> None:
    """Erase every stored copy of a document; the caller holds its map lock.

    Order: the source file (only when ``source_path`` is given), the FAISS
    index, the BM25 index, then the anonymization map. A missing file or
    index counts as already erased. The first tier that fails raises
    :class:`ArtifactErasureError` and later tiers are not attempted.
    """
    steps: list[tuple[str, Callable[[], None]]] = []
    if source_path is not None:
        steps.append(("source_file", lambda: Path(source_path).unlink(missing_ok=True)))
    steps.append(("vector_index", lambda: VectorStore().delete_index(document_id)))
    steps.append(("bm25_index", lambda: BM25Retriever().delete_index(document_id)))
    for tier, erase in steps:
        try:
            erase()
        except Exception as exc:
            raise ArtifactErasureError(tier, exc) from exc
    try:
        await pop_document_map(document_id)
    except Exception as exc:
        raise ArtifactErasureError("anonymization_map", exc) from exc


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
        on_progress: Callable[[int], None] | None = None,
    ) -> None:
        """Create chunks, build anonymization map, and index them in the vector store.

        Chunks are persisted using normalized raw text (no placeholders) so that
        document views and chunk search operate on the original content. PII
        detection is still executed per chunk in order to populate the
        anonymization map and entity counters, but the sanitized strings are not
        stored; masking happens only when chunks are sent to the LLM.

        ``ingestion_status`` stays ``processing`` until every artifact (map,
        FAISS and BM25 indexes) is written, and the delete endpoint refuses
        a processing document, so no artifact is written after an erasure.
        The map is stored under :func:`document_map_lock` after confirming
        the row still exists; a document deleted meanwhile (a delete that
        started before a reprocess) aborts with
        :class:`DocumentDeletedDuringIngestionError` before any index is
        written.
        """
        def _emit(pct: int) -> None:
            if on_progress is not None:
                on_progress(pct)

        detected_language = document.detected_language
        anon_map = AnonymizationMap(document_id=document.id, language=detected_language)

        # Ingestion uses only Presidio + NER (no Ollama). Coreference is
        # handled at masking time via the blocklist mechanism — Ollama would
        # be redundant here and slow down ingestion significantly.
        sanitizer = await create_sanitizer(db, self._settings, enable_ollama=False)

        semantic_chunks = await self._build_chunks(file_format, ingested_text)

        normalizer = TextNormalizer()

        stored_chunks: List[SemanticChunk] = []
        raw_texts_for_index: List[str] = []
        total_entities = 0
        aggregate_type_counts: Dict[str, int] = {}
        per_chunk_spans: Dict[int, List[ResolvedSpan]] = {}

        num_chunks = max(len(semantic_chunks), 1)
        for chunk_idx, semantic_chunk in enumerate(semantic_chunks):
            _emit(int((chunk_idx + 1) * 90 / num_chunks))
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
            for etype, ecount in sanitize_result.entity_type_counts.items():
                aggregate_type_counts[etype] = aggregate_type_counts.get(etype, 0) + ecount
            if sanitize_result.detected_spans:
                per_chunk_spans[semantic_chunk.index] = sanitize_result.detected_spans
            # Persist the sanitized form of the chunk — the field name
            # ``sanitized_text`` carries exactly that contract, and storing
            # the raw value here meant that any PII span the detector
            # missed (spaced IBAN variants, NER-missed names, novel label
            # shapes) would leak through the chat-time string-replace pass
            # untouched because the anon_map never learned about it.
            # Vector / BM25 indexes keep the raw form via ``raw_texts_for_index``
            # so semantic search quality is unaffected, and those indexes
            # stay local.
            sanitized_chunk_text = sanitize_result.sanitized_text
            stored_chunks.append(
                SemanticChunk(
                    text=sanitized_chunk_text,
                    index=semantic_chunk.index,
                    source_page=semantic_chunk.source_page,
                    section_title=semantic_chunk.section_title,
                    char_count=len(sanitized_chunk_text),
                )
            )

        # ``transcription_text`` powers the local document-preview UI,
        # which renders the ORIGINAL text with the per-entity highlight
        # overlays so the user can verify what was detected and where.
        # That preview never leaves the air-gapped zone, so it is safe
        # — and useful — to show the raw form here even though every
        # other on-disk surface (chunks, anon_map, indexes) keeps PII
        # masked or hashed. Concatenating the per-chunk raw text keeps
        # the preview in sync with the chunk boundaries used for
        # entity offset highlighting.
        document.transcription_text = "\n\n".join(raw_texts_for_index)
        document.ocr_confidence = ingestion_confidence

        chunks = await self._persist_chunks(
            db, document.id, stored_chunks, raw_texts_for_index
        )

        detection_rows: list[EntityDetection] = []
        if per_chunk_spans:
            chunk_index_to_id = {c.index: c.id for c in chunks}
            for chunk_index, resolved_spans in per_chunk_spans.items():
                chunk_id = chunk_index_to_id.get(chunk_index)
                if chunk_id is None:
                    continue
                for rs in resolved_spans:
                    detection_rows.append(EntityDetection(
                        document_id=document.id,
                        chunk_id=chunk_id,
                        entity_type=rs.entity_type,
                        placeholder=rs.placeholder,
                        start_offset=rs.start,
                        end_offset=rs.end,
                        score=rs.score,
                    ))
            if detection_rows:
                db.add_all(detection_rows)

        document.chunk_count = len(chunks)
        document.entity_count = total_entities
        await db.commit()
        await db.refresh(document)

        async with document_map_lock(document.id) as lock:
            still_exists = await db.scalar(
                select(Document.id).where(Document.id == document.id)
            )
            if still_exists is None:
                raise DocumentDeletedDuringIngestionError(
                    f"Document {document.id} was deleted during ingestion"
                )
            await lock.ensure_held()
            await set_document_map(document.id, anon_map)

        # Populate the cross-document entity index so future chat queries
        # can route narrowly to the documents that actually contain the
        # entities the user asked about, and refresh the pairwise
        # relationship rows that involve this document so the
        # visualisation graph and the auto-cluster suggester stay in
        # sync. The index only stores HMAC-SHA256 hashes of the
        # originals under the local encryption key — no raw PII is
        # persisted here.
        try:
            await replace_index_for_document(db, document.id, anon_map)
            await recompute_relationships_for_document(db, document.id)
            await db.commit()
        except Exception as exc:
            logging.getLogger(__name__).error(
                "Failed to populate entity index / relationships for document %s: %s",
                document.id,
                type(exc).__name__,
            )
            await db.rollback()

        if total_entities > 0:
            placeholder_samples = list(anon_map.entity_map.values())[:5] if anon_map.entity_map else []
            audit_event = await log_pii_detected(
                db,
                document_id=document.id,
                regulation_ids=list(document.active_regulation_ids or []),
                entity_type_counts=aggregate_type_counts,
                total_count=total_entities,
                extra={"source": "document_ingestion", "language": detected_language},
                document_name=document.original_filename,
                placeholder_samples=placeholder_samples,
            )
            if audit_event is not None and detection_rows:
                await db.execute(
                    update(EntityDetection)
                    .where(EntityDetection.id.in_([r.id for r in detection_rows]))
                    .values(audit_event_id=audit_event.id)
                )
                await db.commit()

        if chunks and raw_texts_for_index:
            _emit(92)
            await asyncio.to_thread(
                self._index_chunks,
                document.id,
                chunks,
                raw_texts_for_index,
            )
            _emit(96)
            await asyncio.to_thread(
                self._index_chunks_bm25,
                document.id,
                chunks,
                raw_texts_for_index,
            )

        document.ingestion_status = "completed"
        document.ingestion_error = None
        document.ingestion_error_code = None
        await db.commit()

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
        raw_texts: Optional[Sequence[str]] = None,
    ) -> List[DocumentChunk]:
        chunks: List[DocumentChunk] = []
        for idx, semantic_chunk in enumerate(semantic_chunks):
            raw_text = (
                raw_texts[idx]
                if raw_texts is not None and idx < len(raw_texts)
                else None
            )
            chunk = DocumentChunk(
                document_id=document_id,
                index=semantic_chunk.index,
                sanitized_text=semantic_chunk.text,
                raw_text=raw_text,
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

    @staticmethod
    async def cleanup_artifacts(document_id: int) -> None:
        """Remove FAISS index, BM25 index, and anonymization map for a document.

        Runs under :func:`document_map_lock`, so a concurrent map edit can
        neither interleave with nor outlive the erasure. Any failure raises a
        :class:`ArtifactCleanupError` naming the document, the tier and the error
        class only, with the original exception suppressed, so the path never
        reaches the error log or the document row.
        """
        async with document_map_lock(document_id):
            try:
                await erase_document_artifacts(document_id)
            except ArtifactErasureError as exc:
                raise ArtifactCleanupError(
                    f"Could not remove {exc.label} for document {document_id} "
                    f"({type(exc.error).__name__})"
                ) from None

