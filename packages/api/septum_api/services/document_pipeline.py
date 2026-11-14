from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from septum_core.anonymization_map import PLACEHOLDER_PATTERN
from septum_core.language_resolution import iso_language_code
from septum_core.span_projection import ProjectedChunk, project, protected_ranges
from septum_core.spans import DetectionResult
from septum_core.text_utils import normalize_unicode
from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..errors import (
    ChunkTooLargeError,
    ContentFreeError,
    DocumentTextMismatchError,
    ExtractedTextTooLargeError,
)
from ..middleware import upload_limits
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
    masking_segments,
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
    SWEEP_LOCK_WAIT_SECONDS,
    DocumentMapErasureError,
    disk_map_document_ids,
    document_map_lock,
    pop_document_map,
    redis_map_document_ids,
    set_document_map,
)
from .entity_index_service import (
    ENTITY_INDEX_VERSION,
    recompute_relationships_for_document,
    replace_index_for_document,
)
from .ingestion.base import IngestionResult, TextBlock, block_starts, join_blocks
from .ollama_detection_runner import clear_detection_cache, local_model_scope
from .pii_gate import ensure_originals_searchable
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


class DocumentRunSupersededError(ContentFreeError, RuntimeError):
    """The document no longer holds this run's claim, so the run may not write to it.

    A newer run claimed it (its run token changed), a restart elsewhere reset
    it to ``failed`` before completion, or the row is gone; the run's own data
    then belongs to neither, so the run counts as failed.
    """


def _holds_claim(document_id: int, run_token: Optional[str], *, require_processing: bool):
    """SQL condition: the document exists and its claim is still the run ``run_token``.

    ``IS NOT DISTINCT FROM``, so a run without a token matches only a row
    without one. ``require_processing`` also requires the ``processing``
    status: an orphan reset keeps the token but sets ``failed``, and a run's
    data must not land on a failed row.
    """
    condition = and_(
        Document.id == document_id,
        Document.ingestion_run_id.is_not_distinct_from(run_token),
    )
    if require_processing:
        condition = and_(condition, Document.ingestion_status == "processing")
    return condition


async def fence_run(
    db: AsyncSession,
    document_id: int,
    run_token: Optional[str],
    *,
    require_processing: bool,
) -> None:
    """Hold the document for run ``run_token`` in the open transaction, or refuse.

    A no-op UPDATE of the row's own token that matches only while the token
    is still ``run_token`` and, with ``require_processing``, the document is
    still ``processing`` (every data write; a failure write passes ``False``
    so a run can still record its failure on a row an orphan reset already
    failed). Called at the start of each transaction a run commits, so the
    writes that follow commit only with it: the UPDATE takes the row lock
    (PostgreSQL) or the database write lock (SQLite), and a newer claim
    either already landed, making this fence fail, or waits for this commit.
    On a mismatch, a deleted row included, the transaction is rolled back and
    :class:`DocumentRunSupersededError` is raised.
    """
    held = await db.scalar(
        update(Document)
        .where(_holds_claim(document_id, run_token, require_processing=require_processing))
        .values(ingestion_run_id=Document.ingestion_run_id)
        .returning(Document.id)
        .execution_options(synchronize_session=False)
    )
    if held is None:
        await db.rollback()
        raise DocumentRunSupersededError(
            "A newer run holds the document; this run's writes were refused."
        )


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

    A run that is then refused (oversize text or chunk, too-deep originals
    pattern) deliberately does not clear the detection reply cache again
    afterwards. It lives in memory only, is keyed per owner and the identical
    text slice (so only the user who already holds that text can read an
    entry), expires after 300 seconds and holds what a successful ingestion
    would have cached. :func:`clear_detection_cache` is global and would
    empty every user's cache, so a per-refusal clear is rejected.
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
            start_char=record.start,
            end_char=record.end,
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
    db: AsyncSession,
    document_id: int,
    anon_map: AnonymizationMap,
    *,
    run_token: Optional[str],
) -> None:
    """Store the map under its lock, refusing when the row was deleted or claimed by a newer run.

    The fence (:func:`fence_run`) stays open until the map is written, so a
    newer claim cannot land between the check and the write.
    """
    async with document_map_lock(document_id) as lock:
        still_exists = await db.scalar(
            select(Document.id).where(Document.id == document_id)
        )
        if still_exists is None:
            raise DocumentDeletedDuringIngestionError(
                f"Document {document_id} was deleted during ingestion"
            )
        await fence_run(db, document_id, run_token, require_processing=True)
        try:
            await lock.ensure_held()
            await set_document_map(document_id, anon_map)
        except BaseException:
            await db.rollback()
            raise
        await db.commit()


async def _refresh_entity_index(
    db: AsyncSession,
    document: Document,
    anon_map: AnonymizationMap,
    *,
    run_token: Optional[str],
) -> bool:
    """Populate the cross-document entity index and relationship rows, and record its version.

    Lets future chat queries route narrowly to the documents that actually
    contain the entities the user asked about, and keeps the visualisation
    graph and the auto-cluster suggester in sync. The index only stores
    HMAC-SHA256 hashes of the originals under the local encryption key —
    no raw PII is persisted here. The rows and ``entity_index_version`` are
    written in one transaction fenced on ``run_token`` (:func:`fence_run`);
    a refused fence raises :class:`DocumentRunSupersededError`. Any other
    failure is logged by error class and rolled back, and the ingestion
    itself still completes. Returns whether the rows were written.
    """
    document_id = document.id
    try:
        await fence_run(db, document_id, run_token, require_processing=True)
        await replace_index_for_document(db, document_id, anon_map)
        await recompute_relationships_for_document(db, document_id)
        document.entity_index_version = ENTITY_INDEX_VERSION
        await db.commit()
    except DocumentRunSupersededError:
        raise
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
    run_token: Optional[str],
) -> None:
    """Record the PII-detected audit event and link the detection rows to it.

    The link is fenced on ``run_token`` (:func:`fence_run`): once a newer
    claim deleted this run's rows, their ids may already belong to the newer
    run's rows. The event itself is a record of a detection that did happen
    and is kept either way.
    """
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
        await fence_run(db, document.id, run_token, require_processing=True)
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
    languages: List[str]


def _masked_languages(language: str, applied: Optional[Sequence[str]]) -> List[str]:
    """Distinct languages a document was masked in: ``language`` first, then ``applied`` in its order.

    ``applied`` is what the detector reports it really treated as segments
    (``DetectionResult.segment_languages``); it is empty when detection ran
    in a single language, whatever segments were requested. Only well-formed
    ISO 639 codes are kept.
    """
    languages = [language]
    for code in applied or ():
        canonical = iso_language_code(code)
        if canonical is not None and canonical not in languages:
            languages.append(canonical)
    return languages


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
        *,
        run_token: Optional[str],
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
        anything is stored, and so does a map whose originals nest past the
        masking depth limit (:class:`OriginalsPatternTooDeepError`, see
        :func:`ensure_originals_searchable`): chat could never mask against
        it.

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

        ``run_token`` is the token the claim that started this run handed
        out, passed in and never re-read from the row. Every transaction the
        run commits on the document (the chunk insert, the detection rows and
        counts, the map store, the entity index with its version, the audit
        link, completion) is fenced by :func:`fence_run` with ``processing``
        required, so once a newer claim holds the document, or an orphan
        reset failed it, this run writes none of them and raises
        :class:`DocumentRunSupersededError`.

        Accepted residual: the FAISS and BM25 files are not fenced. A run
        that passed its last fence before a newer claim can still write them
        after that newer run finished first; they then hold this run's chunk
        ids, which may by then point at rows of another claim or, where the
        database reuses freed ids, of another document, until the document
        is reprocessed. Retrieval for the document can be wrong in that
        window; chunk lookups by id are being hardened separately.
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
        if len(text) > upload_limits.MAX_EXTRACTED_TEXT_CHARS:
            raise ExtractedTextTooLargeError(
                "Extracted text exceeds the MAX_EXTRACTED_TEXT_CHARS limit "
                f"of {upload_limits.MAX_EXTRACTED_TEXT_CHARS} characters."
            )
        judged_override = supported_language_code(document.language_override)
        language, language_source = masking_language(document, text)
        anon_map = AnonymizationMap(document_id=document.id, language=language)

        masked = await self._mask_document(
            sanitizer,
            document,
            file_format,
            blocks,
            text,
            language,
            language_source,
            anon_map,
            emit,
        )
        await asyncio.to_thread(ensure_originals_searchable, anon_map)
        records, projected = masked.records, masked.projected

        # The local preview joins the raw chunk texts and places each
        # detection row by its chunk-relative offsets (see ``_detection_rows``).
        transcription_text = "\n\n".join(r.text for r in records)
        await fence_run(db, document.id, run_token, require_processing=True)
        document.transcription_text = transcription_text
        document.ocr_confidence = ingestion_result.confidence
        chunks = await self._persist_chunks(
            db,
            _chunk_rows(document.id, records, projected, masked.titles, _sheet_numbers(blocks)),
        )

        await fence_run(db, document.id, run_token, require_processing=True)
        detection_rows = _detection_rows(document.id, chunks, projected)
        if detection_rows:
            db.add_all(detection_rows)
        document.chunk_count = len(chunks)
        document.entity_count = masked.entity_count
        await db.commit()
        await db.refresh(document)

        await _store_document_map(db, document.id, anon_map, run_token=run_token)
        await _refresh_entity_index(db, document, anon_map, run_token=run_token)

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
                run_token,
            )

        if chunks:
            await self._index_document(
                document.id, chunks, [r.text for r in records], language, emit
            )

        document.sanitized_entity_types = policy_snapshot.entity_types
        document.sanitized_policy_digest = policy_snapshot.digest
        document.sanitized_language = language
        document.sanitized_languages = masked.languages
        await self._clear_refused_override(
            db, document, text,
            judged=(judged_override, language_source == "override"),
        )
        await self._mark_completed(db, document, run_token)

    @staticmethod
    async def _mark_completed(
        db: AsyncSession, document: Document, run_token: Optional[str]
    ) -> None:
        """Commit the run, setting ``completed`` only on a document still ``processing`` for ``run_token``.

        The status write is a conditional UPDATE that is also this
        transaction's fence (the :func:`fence_run` condition, ``processing``
        required), so a run whose document was reset to ``failed`` (a restart
        in another process) or claimed by a newer run meanwhile never marks
        it ``completed`` over data that is no longer its own: the final
        transaction is rolled back and :class:`DocumentRunSupersededError` is
        raised instead (chunks, map and indexes of this run were already
        written and are left to the failure handler's status-guarded purge).
        The in-memory status is set only after that UPDATE matched, inside
        its transaction.
        """
        marked = await db.execute(
            update(Document)
            .where(_holds_claim(document.id, run_token, require_processing=True))
            .values(ingestion_status="completed", ingestion_error=None, ingestion_error_code=None)
            .execution_options(synchronize_session=False)
        )
        if marked.rowcount != 1:
            await db.rollback()
            raise DocumentRunSupersededError(
                "The document stopped being processed before this run could complete it."
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
        language_source: str,
        anon_map: AnonymizationMap,
        emit: Callable[[int], None],
    ) -> _MaskedDocument:
        """Detect once on ``text``, fill ``anon_map``, cut and project the chunks.

        Language segments are computed from the blocks
        (:func:`~septum_api.utils.db_helpers.masking_segments`) and handed to
        the detector; a user-chosen ``language_source`` gets none. A segment
        error fails the document like any detection error, never falls back
        to one language.
        An empty document has no chunks and calls neither the detector nor
        the embedder. Raises :class:`DocumentTextMismatchError` when the
        detector's normalized text is not ``text`` (the chunk offsets would
        not match the span offsets) and :class:`ChunkTooLargeError` for a
        masked chunk over the limit; neither message holds document text.
        """
        if not text:
            return _MaskedDocument([], [], [], 0, {}, [language])
        boundaries = block_starts(blocks)
        async with local_model_scope(document.user_id, budgeted=False):
            segments = await asyncio.to_thread(
                masking_segments, text, boundaries, language, language_source
            )
            detection = await asyncio.to_thread(
                sanitizer.detect, text, language, boundaries=boundaries, segments=segments
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
            records,
            projected,
            titles,
            applied.entity_count,
            dict(applied.entity_type_counts),
            _masked_languages(language, detection.segment_languages),
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
        """Store ``chunks`` in the caller's open transaction and commit it (the insert assigns the ids).

        :meth:`run` opens that transaction with :func:`fence_run`, so the
        chunks land only together with its fence.
        """
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
    async def cleanup_artifacts(
        document_id: int,
        *,
        db: Optional[AsyncSession] = None,
        run_token: Optional[str] = None,
    ) -> None:
        """Remove FAISS index, BM25 index, and anonymization map for a document.

        Runs under :func:`document_map_lock`, so a concurrent map edit can
        neither interleave with nor outlive the erasure. Any failure raises a
        :class:`ArtifactCleanupError` naming the document, the tier and the error
        class only, with the original exception suppressed, so the path never
        reaches the error log or the document row.

        A run passes its session as ``db``: its claim (``run_token``, still
        ``processing``) is then re-read under the lock with a plain SELECT,
        whose transaction is committed (no row lock, so the map-lock-then-row
        order of the map store holds), and a run that no longer holds the
        document erases nothing and raises
        :class:`DocumentRunSupersededError`. A claim landing after that read
        cleans up under this lock after this erasure, before it writes anything.
        """
        async with document_map_lock(document_id):
            if db is not None:
                held = await db.scalar(
                    select(Document.id).where(
                        _holds_claim(document_id, run_token, require_processing=True)
                    )
                )
                await db.commit()
                if held is None:
                    raise DocumentRunSupersededError(
                        "A newer run holds the document; this run's writes were refused."
                    )
            try:
                await erase_document_artifacts(document_id)
            except ArtifactErasureError as exc:
                raise ArtifactCleanupError(
                    f"Could not remove {exc.label} for document {document_id} "
                    f"({type(exc.error).__name__})"
                ) from None



DERIVED_DATA_RESET: Dict[str, object] = {
    "chunk_count": 0,
    "entity_count": 0,
    "transcription_text": None,
    "sanitized_entity_types": None,
    "sanitized_policy_digest": None,
    "sanitized_language": None,
    "sanitized_languages": None,
}
"""Document columns a run derives from its text, reset whenever its chunks go."""


def _holds_derived_data():
    """SQL condition: the document still holds extracted text, chunks or detection rows."""
    return or_(
        Document.transcription_text.is_not(None),
        Document.id.in_(select(DocumentChunk.document_id)),
        Document.id.in_(select(EntityDetection.document_id)),
    )


def _log_purge_failure(document_id: int, exc: BaseException) -> None:
    logger.error(
        "Purging the derived data of failed document_id=%s did not land: %s",
        document_id,
        type(exc).__name__,
    )


async def _rollback_quietly(db: AsyncSession) -> None:
    try:
        await db.rollback()
    except Exception as exc:
        logger.warning("Rollback after a failed purge did not land: %s", type(exc).__name__)


async def _erase_failed_artifacts(
    db: AsyncSession, document_id: int, *, clear_reply_cache: bool
) -> bool:
    """Erase the indexes and map of a document that is still ``failed``; never raises.

    The status is read again under :func:`document_map_lock`, and the read
    transaction ends before anything is erased. A reprocess claims the
    document before its own cleanup takes that lock, so a document claimed
    meanwhile is left to that run, and one claimed while the lock is held
    rebuilds only after this erasure. The source file is never touched.

    The wait for another worker's lock is :data:`SWEEP_LOCK_WAIT_SECONDS`
    (the process-local lock has no limit, as in the orphan sweep), and the lock
    is confirmed held right before the erasure. A timeout, or a failure before
    the erasure, erases nothing; a lock lost only when it is released comes
    after a successful erasure. Either way the failure is logged by error
    class only and the function returns ``False``, so the rows stay as the
    startup sweep's marker. Returns whether the erasure ran.
    """
    try:
        async with document_map_lock(document_id, wait_seconds=SWEEP_LOCK_WAIT_SECONDS) as lock:
            still_failed = await db.scalar(
                select(Document.id).where(
                    Document.id == document_id, Document.ingestion_status == "failed"
                )
            )
            await db.rollback()
            if still_failed is None:
                return False
            await lock.ensure_held()
            await erase_document_artifacts(document_id, clear_reply_cache=clear_reply_cache)
    except ArtifactErasureError as exc:
        log_artifact_erasure_failure(document_id, exc)
        await _rollback_quietly(db)
        return False
    except Exception as exc:
        _log_purge_failure(document_id, exc)
        await _rollback_quietly(db)
        return False
    return True


async def _purge_failed_rows(db: AsyncSession, document_ids: Sequence[int]) -> List[int]:
    """Drop the chunks, detections and derived columns of the documents still ``failed``.

    The status-guarded UPDATE ... RETURNING runs first and takes the row
    locks, so a document reprocessed since it was checked is skipped and the
    deletes act only on the returned ids. Caller commits.
    """
    locked = await db.execute(
        update(Document)
        .where(Document.id.in_(document_ids), Document.ingestion_status == "failed")
        .values(**DERIVED_DATA_RESET)
        .returning(Document.id)
    )
    still_failed = list(locked.scalars().all())
    if still_failed:
        await db.execute(
            delete(EntityDetection).where(EntityDetection.document_id.in_(still_failed))
        )
        await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id.in_(still_failed)))
    return still_failed


async def purge_failed_derived_data(
    db: AsyncSession, document_id: int, *, clear_reply_cache: bool = True
) -> bool:
    """Erase everything a failed run derived from a document; never raises.

    A reprocess rebuilds it all from the source and a delete erases it, so a
    failed document's chunks (raw and masked text), detection rows, extracted
    text, counts, recorded policy, anonymization map and FAISS / BM25 indexes
    serve no purpose. The indexes and map go first (:func:`_erase_failed_artifacts`),
    then the rows in one status-guarded transaction: if the erasure fails the
    rows stay, marking the document for :func:`purge_failed_documents` at the
    next startup. The document row, its source file, its recorded error and
    its audit events (placeholders only) are kept. A document that holds no
    text, chunk or detection row (a run refused before storing anything) is
    left alone without taking the map lock. Each failure is logged by error
    class only and leaves the ``failed`` status as it is. Returns whether
    rows were purged.
    """
    try:
        candidate = await db.scalar(
            select(Document.id).where(
                Document.id == document_id,
                Document.ingestion_status == "failed",
                _holds_derived_data(),
            )
        )
    except Exception as exc:
        _log_purge_failure(document_id, exc)
        await _rollback_quietly(db)
        return False
    if candidate is None:
        return False
    if not await _erase_failed_artifacts(db, document_id, clear_reply_cache=clear_reply_cache):
        return False
    try:
        purged = await _purge_failed_rows(db, [document_id])
        await db.commit()
    except Exception as exc:
        _log_purge_failure(document_id, exc)
        await _rollback_quietly(db)
        return False
    return bool(purged)


async def _failed_ids_with_derived_data(
    session_maker: async_sessionmaker[AsyncSession],
) -> List[int]:
    """Ids of ``failed`` documents that still hold rows, extracted text, a map or index files.

    The rows are found in one query. The stored map and index files are
    listed once per tier and intersected with the ``failed`` ids, which
    catches what a reprocess left when its own cleanup failed after its
    claim had already cleared the rows; with no ``failed`` document nothing
    is listed. A listing failure is logged by tier and error class, and the
    other tiers still count.
    """
    async with session_maker() as db:
        rows = await db.execute(
            select(Document.id, _holds_derived_data()).where(Document.ingestion_status == "failed")
        )
        failed = {document_id: bool(holds) for document_id, holds in rows}
    if not failed:
        return []
    candidates = {document_id for document_id, holds in failed.items() if holds}
    candidates |= await _stored_artifact_ids() & failed.keys()
    return sorted(candidates)


async def _stored_artifact_ids() -> set[int]:
    """Ids that have a stored FAISS or BM25 index or an anonymization map in any tier."""
    stored: set[int] = set()
    listings: List[Tuple[str, Callable[[], set[int]]]] = [
        ("vector_index", lambda: VectorStore().stored_document_ids()),
        ("bm25_index", lambda: BM25Retriever().stored_document_ids()),
        ("anonymization_map", disk_map_document_ids),
    ]
    for tier, listing in listings:
        try:
            stored |= listing()
        except Exception as exc:
            _log_listing_failure(tier, exc)
    try:
        stored |= await redis_map_document_ids()
    except Exception as exc:
        _log_listing_failure("anonymization_map_cache", exc)
    return stored


def _log_listing_failure(tier: str, exc: BaseException) -> None:
    logger.error(
        "Could not list stored artifacts of failed documents tier=%s error=%s",
        tier,
        type(exc).__name__,
    )


async def purge_failed_documents(
    session_maker: async_sessionmaker[AsyncSession], batch_size: int = 100
) -> int:
    """Purge the derived data every ``failed`` document still holds (startup sweep).

    Covers runs that failed before :func:`purge_failed_derived_data` existed,
    purges whose erasure failed, and runs interrupted by a restart. Each
    document's indexes and map are erased first, without the reply cache;
    then each batch's rows go in one status-guarded transaction, so a
    document reprocessed meanwhile is skipped. One failing document or batch
    is logged by error class and the rest still run. The detection reply
    cache is cleared once at the end. Once clean, a run selects nothing.
    Returns how many documents were purged; only that count is logged.

    Accepted limits: a failed erasure is retried only at the next process
    start (the orphan sweep's model), so its data stays until then; and a
    chunk edit does not check the document status, so it can write a map
    between a purge's erasure and its row delete, which the next sweep
    then removes.
    """
    document_ids = await _failed_ids_with_derived_data(session_maker)
    purged = 0
    erased_any = False
    for start in range(0, len(document_ids), batch_size):
        async with session_maker() as db:
            erased: List[int] = []
            for document_id in document_ids[start : start + batch_size]:
                if await _erase_failed_artifacts(db, document_id, clear_reply_cache=False):
                    erased.append(document_id)
            if not erased:
                continue
            erased_any = True
            try:
                batch_purged = len(await _purge_failed_rows(db, erased))
                await db.commit()
                purged += batch_purged
            except Exception as exc:
                logger.error(
                    "Purging the rows of a failed document batch did not land: %s",
                    type(exc).__name__,
                )
                await _rollback_quietly(db)
    if erased_any:
        try:
            await clear_detection_cache()
        except Exception as exc:
            logger.error("Clearing the detection reply cache did not land: %s", type(exc).__name__)
    if purged:
        logger.info("Purged the derived data of %d failed documents", purged)
    return purged
