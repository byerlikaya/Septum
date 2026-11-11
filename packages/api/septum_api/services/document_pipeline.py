from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from septum_core.anonymization_map import PLACEHOLDER_PATTERN
from septum_core.span_projection import ProjectedChunk, project, protected_ranges
from septum_core.spans import DetectionResult
from septum_core.text_utils import normalize_unicode
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import ChunkTooLargeError, ContentFreeError, DocumentTextMismatchError
from ..models.document import Chunk as DocumentChunk
from ..models.document import Document
from ..models.entity_detection import EntityDetection
from ..models.settings import AppSettings
from ..request_limits import (
    MAX_PRE_APPROVED_CHUNK_CHARS,
    MAX_SECTION_TITLE_CHARS,
    MIN_PDF_CHUNK_SIZE,
)
from ..utils.db_helpers import (
    masking_language,
    override_fits_text,
    supported_language_code,
)
from .anonymization_map import AnonymizationMap
from .audit_logger import (
    OVERRIDE_REFUSED_SCRIPT_MISMATCH,
    language_override_event,
    log_pii_detected,
)
from .bm25_retriever import BM25Retriever, bm25_index_lock
from .chunking import ChunkRecord, chunk_blocks
from .document_anon_store import (
    DocumentMapErasureError,
    document_map_lock,
    pop_document_map,
    set_document_map,
)
from .entity_index_service import (
    ENTITY_INDEX_VERSION,
    recompute_relationships_for_document,
    replace_index_for_document,
)
from .ingestion.base import IngestionResult, TextBlock, block_starts, join_blocks
from .ollama_detection_runner import clear_detection_cache, local_model_scope
from .policy_composer import PolicyComposer
from .remask_cache import remask_cache
from .sanitizer import PIISanitizer, ResolvedSpan
from .sanitizer_factory import DOCUMENT_SANITIZER_ENABLE_OLLAMA, create_sanitizer
from .text_normalizer import TextNormalizer
from .vector_store import VectorStore

logger = logging.getLogger(__name__)


class DocumentDeletedDuringIngestionError(RuntimeError):
    """The document row disappeared while its artifacts were being written."""


_ARTIFACT_TIER_LABELS = {
    "vector_index": "FAISS index",
    "bm25_index": "BM25 index",
    "anonymization_map": "anonymization map",
    "detection_cache": "local-model detection cache",
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


def log_artifact_erasure_failure(document_id: int, exc: ArtifactErasureError) -> None:
    """Log a failed erasure by document id, tier and error class only.

    A map erasure failure is skipped: :func:`pop_document_map` already
    logged it with the tiers that still hold the map.
    """
    if isinstance(exc.error, DocumentMapErasureError):
        return
    logger.error(
        "Could not erase document_id=%s tier=%s error=%s errno=%s",
        document_id,
        exc.tier,
        type(exc.error).__name__,
        getattr(exc.error, "errno", None),
    )


async def erase_document_artifacts(
    document_id: int,
    *,
    source_path: Optional[str] = None,
    clear_reply_cache: bool = True,
) -> None:
    """Erase every stored copy of a document; the caller holds its map lock.

    Order: the source file (only when ``source_path`` is given), the FAISS
    index, the BM25 index, the anonymization map, then the local-model
    detection reply cache of every worker (:func:`clear_detection_cache`),
    last so a reply cached while the erasure ran is dropped too. A missing
    file or index counts as already erased. The first tier that fails
    raises :class:`ArtifactErasureError` and later tiers are not attempted.
    This worker's re-masked chunk cache entries of the document are
    dropped first. ``clear_reply_cache=False`` skips the reply cache tier;
    a caller erasing many documents at once then clears it once, after
    the last of them.
    """
    remask_cache.forget_document(document_id)
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
    if not clear_reply_cache:
        return
    try:
        await clear_detection_cache()
    except DocumentMapErasureError as exc:
        raise ArtifactErasureError("detection_cache", exc) from exc


def _progress_emitter(on_progress: Callable[[int], None] | None) -> Callable[[int], None]:
    """Return a progress callback that is a no-op when no listener was given."""

    def _emit(pct: int) -> None:
        if on_progress is not None:
            on_progress(pct)

    return _emit


_STRATEGY_BY_FORMAT: Dict[str, str] = {
    "pdf": "structured",
    "docx": "structured",
    "txt": "structured",
    "xlsx": "table",
    "ods": "table",
    "csv": "table",
    "tsv": "table",
    "audio": "audio",
}
"""Chunking strategy per file format; any other format uses ``sliding``."""

_PAGINATED_STRATEGY_FORMATS = frozenset({"pdf", "docx"})
"""Formats whose structured chunks use the PDF/DOCX chunk size setting."""


def _row_texts(block: TextBlock) -> List[str]:
    """The rows of a ``rows`` block, without the newline that separates them."""
    text, offsets = block.text, list(block.row_offsets or [])
    ends = [start - 1 for start in offsets[1:]] + [len(text)]
    return [text[start:end] for start, end in zip(offsets, ends)]


def _normalized_blocks(
    blocks: Sequence[TextBlock], rules: Callable[[str], str]
) -> List[TextBlock]:
    """Each block NFC-composed, then normalized by ``rules``, then composed again.

    A ``rows`` block is normalized row by row and its ``row_offsets`` are
    rebuilt, so a rule that changes a row's length never shifts the next
    row's start.
    """

    def normalize(text: str) -> str:
        return normalize_unicode(rules(normalize_unicode(text)))

    out: List[TextBlock] = []
    for block in blocks:
        if block.kind == "rows" and block.row_offsets:
            rows = [normalize(row) for row in _row_texts(block)]
            offsets: List[int] = []
            position = 0
            for row in rows:
                offsets.append(position)
                position += len(row) + 1
            out.append(replace(block, text="\n".join(rows), row_offsets=offsets))
        else:
            out.append(replace(block, text=normalize(block.text)))
    return out


def _sheet_numbers(blocks: Sequence[TextBlock]) -> Dict[str, int]:
    """One-based number of each sheet, in order of first appearance.

    Chunks store this number, never the sheet name: a name can hold
    personal data, and its masked form lives in the chunk's section title.
    """
    numbers: Dict[str, int] = {}
    for block in blocks:
        if block.sheet is not None and block.sheet not in numbers:
            numbers[block.sheet] = len(numbers) + 1
    return numbers


def _cut_title(masked: str) -> str:
    """``masked`` cut to ``MAX_SECTION_TITLE_CHARS``, before a placeholder the cut would split."""
    if len(masked) <= MAX_SECTION_TITLE_CHARS:
        return masked
    for match in PLACEHOLDER_PATTERN.finditer(masked):
        if match.start() < MAX_SECTION_TITLE_CHARS < match.end():
            return masked[: match.start()]
    return masked[:MAX_SECTION_TITLE_CHARS]


def _masked_titles(
    detection: DetectionResult,
    resolved: Sequence[ResolvedSpan],
    anon_map: AnonymizationMap,
    records: Sequence[ChunkRecord],
) -> List[Optional[str]]:
    """Every chunk's section title, masked.

    The whole title source ``[section_title_start, section_title_end)`` is
    projected with the document spans, so a value the raw title's cut
    would split is masked whole, and only then cut to length. A title
    without a usable source range is dropped, never stored raw.
    """
    sources = sorted(
        {
            (r.section_title_start, r.section_title_end)
            for r in records
            if r.section_title
            and r.section_title_start is not None
            and r.section_title_end is not None
            and r.section_title_start < r.section_title_end
        }
    )
    masked = {
        (p.start, p.end): _cut_title(p.masked_text)
        for p in (project(detection, resolved, sources, anon_map) if sources else [])
    }
    return [
        masked.get((r.section_title_start, r.section_title_end)) if r.section_title else r.section_title
        for r in records
    ]


def _require_fitting(projected: Sequence[ProjectedChunk]) -> None:
    """Refuse the document when a masked chunk exceeds the chunk limit.

    A chunk may outgrow its budget only because one protected range is
    longer; its masked text is usually shorter, and is never truncated.
    """
    if any(len(p.masked_text) > MAX_PRE_APPROVED_CHUNK_CHARS for p in projected):
        raise ChunkTooLargeError(
            f"A chunk is longer than {MAX_PRE_APPROVED_CHUNK_CHARS} characters "
            "after masking; the document was not stored."
        )


def _chunk_rows(
    document_id: int,
    records: Sequence[ChunkRecord],
    projected: Sequence[ProjectedChunk],
    titles: Sequence[Optional[str]],
    sheet_numbers: Dict[str, int],
) -> List[DocumentChunk]:
    """The chunk rows to store: masked text, raw slice, position metadata, masked title."""
    return [
        DocumentChunk(
            document_id=document_id,
            index=record.index,
            sanitized_text=chunk.masked_text,
            raw_text=record.text,
            char_count=len(chunk.masked_text),
            source_page=record.page,
            source_slide=None,
            source_sheet=sheet_numbers.get(record.sheet) if record.sheet is not None else None,
            source_timestamp_start=record.t_start,
            source_timestamp_end=record.t_end,
            section_title=title,
        )
        for record, chunk, title in zip(records, projected, titles)
    ]


def _detection_rows(
    document_id: int,
    chunks: Sequence[DocumentChunk],
    projected: Sequence[ProjectedChunk],
) -> List[EntityDetection]:
    """One detection row per span a stored chunk shows, offsets relative to its raw text.

    A span cut by a chunk edge gets a row in every chunk it touches, with
    the part inside that chunk, so the preview highlights what each chunk
    masks.
    """
    return [
        EntityDetection(
            document_id=document_id,
            chunk_id=chunk.id,
            entity_type=span.entity_type,
            placeholder=span.placeholder,
            start_offset=span.start,
            end_offset=span.end,
            score=span.score,
        )
        for chunk, projection in zip(chunks, projected)
        for span in projection.spans
    ]


async def _store_document_map(
    db: AsyncSession, document_id: int, anon_map: AnonymizationMap
) -> None:
    """Store the map under its lock, refusing when the row was deleted meanwhile."""
    async with document_map_lock(document_id) as lock:
        still_exists = await db.scalar(
            select(Document.id).where(Document.id == document_id)
        )
        if still_exists is None:
            raise DocumentDeletedDuringIngestionError(
                f"Document {document_id} was deleted during ingestion"
            )
        await lock.ensure_held()
        await set_document_map(document_id, anon_map)


async def _refresh_entity_index(
    db: AsyncSession, document_id: int, anon_map: AnonymizationMap
) -> bool:
    """Populate the cross-document entity index and relationship rows.

    Lets future chat queries route narrowly to the documents that actually
    contain the entities the user asked about, and keeps the visualisation
    graph and the auto-cluster suggester in sync. The index only stores
    HMAC-SHA256 hashes of the originals under the local encryption key —
    no raw PII is persisted here. A failure is logged by error class and
    rolled back; the ingestion itself still completes. Returns whether the
    rows were written, so the caller records the index version only then.
    """
    try:
        await replace_index_for_document(db, document_id, anon_map)
        await recompute_relationships_for_document(db, document_id)
        await db.commit()
    except Exception as exc:
        logger.error(
            "Failed to populate entity index / relationships for document %s: %s",
            document_id,
            type(exc).__name__,
        )
        await db.rollback()
        return False
    return True


async def _audit_detections(
    db: AsyncSession,
    document: Document,
    type_counts: Dict[str, int],
    total_entities: int,
    anon_map: AnonymizationMap,
    extra: Dict[str, str],
    detection_rows: Sequence[EntityDetection],
) -> None:
    """Record the PII-detected audit event and link the detection rows to it."""
    placeholder_samples = list(anon_map.entity_map.values())[:5] if anon_map.entity_map else []
    audit_event = await log_pii_detected(
        db,
        document_id=document.id,
        regulation_ids=list(document.active_regulation_ids or []),
        entity_type_counts=type_counts,
        total_count=total_entities,
        extra=extra,
        placeholder_samples=placeholder_samples,
    )
    if audit_event is not None and detection_rows:
        await db.execute(
            update(EntityDetection)
            .where(EntityDetection.id.in_([r.id for r in detection_rows]))
            .values(audit_event_id=audit_event.id)
        )
        await db.commit()


@dataclass(frozen=True)
class _MaskedDocument:
    """One run's detection over the whole document and the chunks cut from it."""

    records: List[ChunkRecord]
    projected: List[ProjectedChunk]
    titles: List[Optional[str]]
    entity_count: int
    type_counts: Dict[str, int]


class DocumentPipeline:
    """Orchestrates sanitization, chunking, and indexing for ingested documents."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    async def run(
        self,
        db: AsyncSession,
        document: Document,
        file_format: str,
        ingestion_result: IngestionResult,
        on_progress: Callable[[int], None] | None = None,
    ) -> None:
        """Detect PII once on the whole document, cut chunks, store and index them.

        Flow: every block is normalized (NFC, then the text normalization
        rules) and the blocks are joined; the masking language is decided on
        that text; detection runs once over it (windowed above the core
        threshold) and placeholders are assigned in document order. The
        chunker then cuts outside the protected ranges, and each chunk is
        projected: ``sanitized_text`` is its masked form, ``raw_text`` the
        raw slice (preview and indexes only, never sent on). A value cut by
        a chunk edge shows its whole placeholder in both chunks. Entity
        counts are those of the whole document, so overlapping chunks never
        count a value twice. A chunk still longer than the chunk limit after
        masking fails the run with :class:`ChunkTooLargeError` before
        anything is stored.

        ``ingestion_status`` stays ``processing`` until every artifact (map,
        FAISS and BM25 indexes) is written, and the delete endpoint refuses
        a processing document, so no artifact is written after an erasure.
        The map is stored under :func:`document_map_lock` after confirming
        the row still exists; a document deleted meanwhile (a delete that
        started before a reprocess) aborts with
        :class:`DocumentDeletedDuringIngestionError` before any index is
        written.

        The policy is composed once, and its snapshot (entity types and
        policy digest) is recorded when the run completes, so staleness is
        judged against the policy these chunks were actually masked under.
        """
        emit = _progress_emitter(on_progress)

        remask_cache.forget_document(document.id)

        # The Ollama alias layer and pronoun coreference stay off at
        # ingestion; the Ollama validation and contextual layers each follow
        # their own stored setting. Chat re-masks outdated chunks with the
        # same configuration.
        policy, policy_snapshot = await PolicyComposer().compose_with_snapshot(
            db, self._settings
        )
        sanitizer = await create_sanitizer(
            db,
            self._settings,
            enable_ollama=DOCUMENT_SANITIZER_ENABLE_OLLAMA,
            policy=policy,
        )

        rules = await TextNormalizer().compile(db)
        blocks = _normalized_blocks(ingestion_result.blocks, rules)
        text = join_blocks(blocks)
        judged_override = supported_language_code(document.language_override)
        language, language_source = masking_language(document, text)
        anon_map = AnonymizationMap(document_id=document.id, language=language)

        masked = await self._mask_document(
            sanitizer, document, file_format, blocks, text, language, anon_map, emit
        )
        records, projected = masked.records, masked.projected

        # The local preview joins the raw chunk texts and places each
        # detection row by its chunk-relative offsets (see ``_detection_rows``).
        transcription_text = "\n\n".join(r.text for r in records)
        document.transcription_text = transcription_text
        document.ocr_confidence = ingestion_result.confidence

        chunks = await self._persist_chunks(
            db,
            _chunk_rows(document.id, records, projected, masked.titles, _sheet_numbers(blocks)),
        )

        detection_rows = _detection_rows(document.id, chunks, projected)
        if detection_rows:
            db.add_all(detection_rows)

        document.chunk_count = len(chunks)
        document.entity_count = masked.entity_count
        await db.commit()
        await db.refresh(document)

        await _store_document_map(db, document.id, anon_map)
        if await _refresh_entity_index(db, document.id, anon_map):
            document.entity_index_version = ENTITY_INDEX_VERSION

        if masked.entity_count > 0:
            await _audit_detections(
                db,
                document,
                masked.type_counts,
                masked.entity_count,
                anon_map,
                {
                    "source": "document_ingestion",
                    "language": language,
                    "language_source": language_source,
                },
                detection_rows,
            )

        if chunks:
            await self._index_document(
                document.id, chunks, [r.text for r in records], language, emit
            )

        document.sanitized_entity_types = policy_snapshot.entity_types
        document.sanitized_policy_digest = policy_snapshot.digest
        document.sanitized_language = language
        await self._clear_refused_override(
            db, document, text,
            judged=(judged_override, language_source == "override"),
        )
        document.ingestion_status = "completed"
        document.ingestion_error = None
        document.ingestion_error_code = None
        await db.commit()

    async def _mask_document(
        self,
        sanitizer: PIISanitizer,
        document: Document,
        file_format: str,
        blocks: Sequence[TextBlock],
        text: str,
        language: str,
        anon_map: AnonymizationMap,
        emit: Callable[[int], None],
    ) -> _MaskedDocument:
        """Detect once on ``text``, fill ``anon_map``, cut and project the chunks.

        An empty document has no chunks and calls neither the detector nor
        the embedder. Raises :class:`DocumentTextMismatchError` when the
        detector's normalized text is not ``text`` (the chunk offsets would
        not match the span offsets) and :class:`ChunkTooLargeError` for a
        masked chunk over the limit; neither message holds document text.
        """
        if not text:
            return _MaskedDocument([], [], [], 0, {})
        async with local_model_scope(document.user_id, budgeted=False):
            detection = await asyncio.to_thread(
                sanitizer.detect, text, language, boundaries=block_starts(blocks)
            )
            if detection.text != text:
                raise DocumentTextMismatchError(
                    "The detector normalized the document text differently from its blocks."
                )
            emit(60)
            applied = await asyncio.to_thread(sanitizer.apply, detection, anon_map)
        resolved = applied.detected_spans
        emit(70)

        def cut_and_project() -> Tuple[List[ChunkRecord], List[ProjectedChunk]]:
            ranges = protected_ranges(detection, resolved, anon_map)
            records = self._chunk_records(file_format, blocks, ranges)
            return records, project(detection, resolved, [(r.start, r.end) for r in records], anon_map)

        records, projected = await asyncio.to_thread(cut_and_project)
        _require_fitting(projected)
        titles = _masked_titles(detection, resolved, anon_map, records)
        emit(90)
        return _MaskedDocument(
            records, projected, titles, applied.entity_count, dict(applied.entity_type_counts)
        )

    def _chunk_records(
        self,
        file_format: str,
        blocks: Sequence[TextBlock],
        protected: Sequence[Tuple[int, int]],
    ) -> List[ChunkRecord]:
        """Cut the document with its format's strategy and the character settings.

        Table and audio chunks are sized by cells and seconds; structured
        and sliding chunks by the format's character size and overlap.
        """
        settings = self._settings
        strategy = _STRATEGY_BY_FORMAT.get(file_format, "sliding")
        if strategy == "table":
            return chunk_blocks(
                blocks, "table", protected=protected,
                spreadsheet_chunk_size=settings.spreadsheet_chunk_size,
            )
        if strategy == "audio":
            return chunk_blocks(
                blocks, "audio", protected=protected,
                audio_chunk_size=float(settings.audio_chunk_size),
            )
        if file_format in _PAGINATED_STRATEGY_FORMATS:
            chunk_size = max(settings.pdf_chunk_size, MIN_PDF_CHUNK_SIZE)
        else:
            chunk_size = settings.chunk_size
        return chunk_blocks(
            blocks, strategy, protected=protected,
            chunk_size=chunk_size, overlap=settings.chunk_overlap,
        )

    async def _index_document(
        self,
        document_id: int,
        chunks: Sequence[DocumentChunk],
        raw_texts: Sequence[str],
        language: str,
        emit: Callable[[int], None],
    ) -> None:
        """Build the FAISS and BM25 indexes over the raw chunk texts."""
        emit(92)
        await asyncio.to_thread(self._index_chunks, document_id, chunks, raw_texts)
        emit(96)
        async with bm25_index_lock(document_id):
            await asyncio.to_thread(
                self._index_chunks_bm25, document_id, chunks, raw_texts, language
            )

    @staticmethod
    async def _clear_refused_override(
        db: AsyncSession,
        document: Document,
        transcription_text: str,
        judged: Optional[Tuple[Optional[str], bool]] = None,
    ) -> None:
        """Clear the stored override if this run's text contradicts its script.

        Staleness reads the override without the text, so a contradicted
        override left in place would keep the document outdated after every
        run. The value is re-read here rather than taken from the start of
        the run: a ``PATCH`` accepted while the document had no text yet was
        never checked, so whatever is stored now is judged against the final
        text. One that fits is kept (if it is not the language this run
        used, the document is stale). The clear only matches the value
        judged, so a ``PATCH`` landing in between is never overwritten, and
        it is audited in the same transaction. ``judged`` is the run's own
        ``(override, fits)`` verdict on the same text, reused when the stored
        override is still that one.
        """
        current = (
            await db.execute(
                select(Document.language_override).where(Document.id == document.id)
            )
        ).scalar_one_or_none()
        code = supported_language_code(current)
        if code is None:
            return
        if judged is not None and judged[0] == code:
            fits = judged[1]
        else:
            fits = override_fits_text(code, transcription_text)
        if fits:
            return
        result = await db.execute(
            update(Document)
            .where(Document.id == document.id, Document.language_override == current)
            .values(language_override=None)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            return
        logger.warning("Language override %s contradicts document script; cleared", code)
        document.language_override = None
        db.add(
            language_override_event(
                document.id, None, code, None, reason=OVERRIDE_REFUSED_SCRIPT_MISMATCH
            )
        )

    async def _persist_chunks(
        self, db: AsyncSession, chunks: Sequence[DocumentChunk]
    ) -> List[DocumentChunk]:
        """Store ``chunks``; the insert assigns each its id (sessions keep loaded state on commit)."""
        db.add_all(chunks)
        await db.commit()
        return list(chunks)

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
        language: str,
    ) -> None:
        """Build BM25 index for keyword-based retrieval, tokenized in the masking language."""
        if len(chunks) != len(raw_texts):
            return

        bm25_retriever = BM25Retriever()
        bm25_retriever.index_document(
            document_id,
            [c.id for c in chunks],
            list(raw_texts),
            language,
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

