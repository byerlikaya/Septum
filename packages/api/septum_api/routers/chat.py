from __future__ import annotations

"""FastAPI router for chat with full privacy-preserving RAG pipeline.

Pipeline:
    sanitize (query) → retrieve chunks → optional approval gate →
    LLM (cloud) → local de-anonymization → SSE response.
"""

import asyncio
import contextlib
import copy
import json
import logging
import re
import time
from dataclasses import dataclass, field
from itertools import zip_longest
from typing import (
    TYPE_CHECKING,
    AbstractSet,
    Any,
    AsyncGenerator,
    Awaitable,
    Callable,
    List,
    Optional,
    Sequence,
    Tuple,
)
from uuid import uuid4

if TYPE_CHECKING:
    from ..services.bm25_retriever import BM25Retriever
    from ..services.vector_store import VectorStore

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def _phase_timer(session_id: str, phase: str) -> AsyncGenerator[None, None]:
    """Log how long a chat pipeline phase takes.

    Wraps a section of :func:`_chat_event_stream` so that each major phase
    (sanitize, retrieve, mask, approval-wait, llm, deanonymize, …) emits a
    single ``chat phase session_id=… phase=… elapsed_ms=…`` log line on
    completion. The structured fields make it trivial to grep a hung
    request's session_id in stdout / Error Logs and see which phase was the
    last to start (and how long it took).

    The timer also fires on exception so a phase that raises still records
    its elapsed time before the exception propagates.
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        logger.info(
            "chat phase session_id=%s phase=%s elapsed_ms=%.1f",
            session_id,
            phase,
            elapsed_ms,
        )


from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from langdetect import DetectorFactory
from pydantic import BaseModel, Field, model_validator
from septum_core.model_integrity import ModelRefusal
from septum_core.placeholder_space import (
    QUERY_MAP_KEY,
    map_placeholders,
    settle_strays,
    unify_placeholder_spaces,
)
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..database import get_db
from ..errors import MODEL_REFUSED_DETAIL
from ..middleware.rate_limit import route_limit
from ..models.document import Chunk, Document
from ..models.regulation import RegulationRuleset
from ..models.settings import AppSettings
from ..models.spreadsheet_schema import (
    SpreadsheetColumn,
    SpreadsheetSchema,
    generated_technical_label,
)
from ..models.user import User
from ..request_limits import (
    MAX_CHAT_MESSAGE_CHARS,
    MAX_CONTEXT_CHUNKS,
    MAX_PRE_APPROVED_CHUNK_CHARS,
    MAX_RETRY_DOCUMENTS,
    MAX_SCHEMA_LABEL_CHARS,
    MAX_SCHEMA_LABELED_COLUMNS,
    MAX_SESSION_ID_LENGTH,
    TOP_K_RETRIEVAL_MAX_CAP,
    DocumentId,
)
from ..services.anonymization_map import (
    PLACEHOLDER_PATTERN,
    PLACEHOLDER_SHAPE,
    AnonymizationMap,
)
from ..services.approval_gate import (
    ApprovalChunk,
    ApprovalGate,
    ApprovalSessionForbiddenError,
    get_approval_gate,
)
from ..services.audit_logger import log_deanonymization, log_pii_detected
from ..services.chat_context import ChatContextPayload, build_chat_prompt
from ..services.chat_debug_store import (
    ChatDebugRecord,
    get_chat_debug_record,
    new_debug_id,
    set_chat_debug_record,
)
from ..services.deanonymizer import Deanonymizer
from ..services.document_anon_store import get_document_map
from ..services.document_cluster_service import cluster_documents_by_relationship
from ..services.entity_index_service import (
    RELATIONSHIP_THRESHOLD_MEDIUM,
    RELATIONSHIP_THRESHOLD_STRONG,
    find_documents_for_query_entities,
)
from ..services.error_logger import (
    log_backend_error,
    log_backend_message,
    report_error_log_failure,
)
from ..services.llm_errors import LLM_UNAVAILABLE
from ..services.llm_router import LLMRouter, LLMRouterError, _chunk_text
from ..services.pii_gate import (
    DocumentGoneError,
    DocumentMapConflictError,
    DocumentMapMissingError,
    gate_untrusted_texts,
    mask_known_entities,
    remask_document_texts,
    reserve_placeholder_counters,
)
from ..services.policy_composer import PolicyComposer
from ..services.policy_snapshot import (
    PolicySnapshot,
    current_policy_snapshot,
    document_staleness,
)
from ..services.prompts import PromptCatalog
from ..services.remask_cache import remask_cache
from ..services.retry_numbering import (
    MAX_NUMBERING_TOKEN_LENGTH,
    numbering_fingerprint,
    sign_numbering,
    verify_numbering,
)
from ..services.sanitizer_factory import (
    DOCUMENT_SANITIZER_ENABLE_OLLAMA,
    create_sanitizer,
)
from ..services.text_normalizer import TextNormalizer
from ..utils.auth_dependency import get_current_user
from ..utils.db_helpers import (
    detect_language,
    document_language,
    effective_masking_language,
    get_owned_or_404,
    load_settings,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])


DetectorFactory.seed = 42


RETRY_NUMBERING_CHANGED = "retry_numbering_changed"
DOCUMENT_CHANGED = "document_changed"
MAP_CONFLICT = "map_conflict"
LLM_UNAVAILABLE_MESSAGE = (
    "The language model could not answer right now; please try again later."
)


class PreApprovedChunk(BaseModel):
    """A chunk the client sends back on a retry.

    ``document_id`` names the document the server offered the chunk from
    (``None`` for text the client added). It is client-supplied, so the
    endpoint owner-checks it before it is used.
    """

    text: str = Field(default="", max_length=MAX_PRE_APPROVED_CHUNK_CHARS)
    document_id: Optional[DocumentId] = None


class ChatRequest(BaseModel):
    """Incoming chat request from the frontend.

    This model supports both the newer v5-style payload
    (``message``, ``document_id``) and the legacy STEP 14
    payload shape (``query``, ``document_ids``, etc.).
    """

    message: Optional[str] = Field(default=None, max_length=MAX_CHAT_MESSAGE_CHARS)
    document_id: Optional[DocumentId] = None
    top_k: Optional[int] = None
    session_id: Optional[str] = Field(default=None, max_length=MAX_SESSION_ID_LENGTH)

    query: Optional[str] = Field(default=None, max_length=MAX_CHAT_MESSAGE_CHARS)
    # A turn offers at most MAX_CONTEXT_CHUNKS chunks, so more documents than
    # that could never all contribute; the bound also caps the owner checks.
    document_ids: Optional[List[DocumentId]] = Field(default=None, max_length=MAX_RETRY_DOCUMENTS)
    output_mode: Optional[str] = "chat"
    require_approval: Optional[bool] = None
    deanon_enabled: Optional[bool] = None
    pre_approved_chunks: Optional[List[PreApprovedChunk]] = Field(
        default=None, max_length=MAX_CONTEXT_CHUNKS
    )
    # Token from the ``approval_required`` event of the turn being retried;
    # it commits to the placeholder numbering the chunks were produced under.
    numbering_token: Optional[str] = Field(
        default=None, max_length=MAX_NUMBERING_TOKEN_LENGTH
    )
    # Optional override produced by the disambiguation picker. When set,
    # entity-aware narrowing is bypassed and retrieval is restricted to
    # exactly this set of documents. The frontend obtains the value from
    # the ``analyze_query`` endpoint and resends the message with the
    # user's chosen cluster.
    scoped_doc_ids: Optional[List[DocumentId]] = Field(default=None, max_length=MAX_RETRY_DOCUMENTS)

    @model_validator(mode="after")
    def _limit_retry_documents(self) -> "ChatRequest":
        """Refuse a retry that names more distinct documents than a turn can offer."""
        ids = {
            c.document_id
            for c in self.pre_approved_chunks or []
            if c.document_id is not None
        }
        if len(ids) > MAX_RETRY_DOCUMENTS:
            raise ValueError(
                f"pre_approved_chunks may reference at most {MAX_RETRY_DOCUMENTS} documents."
            )
        return self


class ChatDebugResponse(BaseModel):
    """Masked debug payload: exactly what crossed the cloud boundary for one turn."""

    debug_id: str
    masked_prompt: str
    masked_answer: str


def _log_stream_failure(exc: Exception) -> None:
    """Log a failed chat stream by refusal code or exception class only.

    The message and traceback are left out on purpose: they can carry text
    from the user's documents or the model response.
    """
    if isinstance(exc, ModelRefusal):
        logger.error("Chat refused, a model was refused (code: %s)", exc.code)
    else:
        logger.error("Unhandled exception in chat event stream: %s", type(exc).__name__)


async def _active_regulations(db: AsyncSession) -> List[Tuple[str, bool]]:
    """Return ``(display_name, is_builtin)`` for every active ruleset."""
    result = await db.execute(
        select(RegulationRuleset.display_name, RegulationRuleset.is_builtin).where(
            RegulationRuleset.is_active.is_(True)
        )
    )
    return [(row[0], bool(row[1])) for row in result.all()]


async def _prompt_regulation_names(
    regulations: List[Tuple[str, bool]],
    anon_map: AnonymizationMap,
    db: AsyncSession,
    settings: AppSettings,
) -> List[str]:
    """Return the regulation names to put in the cloud prompt.

    Built-in names are fixed seed data. Custom ruleset names are typed by
    users, so they pass the PII gate before reaching the cloud LLM.
    """
    custom = [name for name, is_builtin in regulations if not is_builtin]
    gated = iter(await gate_untrusted_texts(custom, anon_map, db, settings))
    return [name if is_builtin else next(gated) for name, is_builtin in regulations]


async def _gate_edited_chunks(
    offered_texts: List[str],
    returned_texts: List[str],
    anon_map: AnonymizationMap,
    db: AsyncSession,
    settings: AppSettings,
) -> List[str]:
    """Gate the chunk texts the approval modal sent back.

    Texts identical to one the server offered were already masked and pass
    through unchanged; any edited or added text goes through the PII gate.
    """
    offered = set(offered_texts)
    edited_idx = [i for i, t in enumerate(returned_texts) if t not in offered]
    if not edited_idx:
        return list(returned_texts)
    gated = await gate_untrusted_texts(
        [returned_texts[i] for i in edited_idx], anon_map, db, settings
    )
    result = list(returned_texts)
    for i, text in zip(edited_idx, gated):
        result[i] = text
    return result


def _auto_rag_owner_filter(user: User):
    """Return the ownership predicate for documents chosen *by the server*.

    It governs auto-RAG candidates in ``/ask`` and the clusters
    ``/analyze_query`` proposes — never documents the client names. Regular
    users get only documents they uploaded; admins additionally get
    ownerless legacy documents (rows predating per-user ownership), but
    never other users' documents.

    Explicitly requested ``document_ids`` (and retry chunk document ids)
    follow ``get_owned_or_404`` instead, where an admin may open any
    document, exactly as the documents router allows.
    """
    if user.role == "admin":
        return or_(Document.user_id == user.id, Document.user_id.is_(None))
    return Document.user_id == user.id


def _effective_require_approval(
    settings_flag: bool,
    request_flag: Optional[bool],
    rag_mode: str,
) -> bool:
    """Decide whether this turn must pass the approval gate.

    The request can only make approval stricter: when the global setting
    requires approval, a client-sent ``require_approval=false`` is
    ignored. Turns without document context (``rag_mode == "none"``) send
    only the sanitized question and skip the gate, as before.
    """
    if rag_mode == "none":
        return False
    return bool(settings_flag) or bool(request_flag)


async def _sanitize_query(
    message: str,
    language: str,
    settings: AppSettings,
    anon_map: AnonymizationMap,
    db: AsyncSession,
    enable_ollama: bool = False,
) -> tuple[str, int, dict[str, int]]:
    """Run the sanitizer on the incoming user message.

    Defaults to ``enable_ollama=False`` because the alias/pronoun layers add
    several seconds of latency per call. The user query is short and its own
    PII is reliably caught by Presidio + NER. The document's anon_map is
    already populated during ingestion (where Ollama runs once).
    """
    sanitizer = await create_sanitizer(db, settings, enable_ollama=enable_ollama)
    result = await asyncio.to_thread(
        sanitizer.sanitize,
        message,
        language,
        anon_map,
    )
    return result.sanitized_text, result.entity_count, result.entity_type_counts


def _chunks_by_turn_document(
    chunks: Sequence[Chunk], documents_by_id: dict[int, Document]
) -> list[tuple[Document, list[Chunk]]]:
    """Group ``chunks`` under their document, in document id order.

    Raises :class:`LookupError` for a chunk whose document is not one of the
    turn's documents.
    """
    by_document: dict[int, list[Chunk]] = {}
    for c in chunks:
        by_document.setdefault(c.document_id, []).append(c)
    docs: list[tuple[Document, list[Chunk]]] = []
    for did in sorted(by_document):
        doc = documents_by_id.get(did)
        if doc is None:
            raise LookupError(f"Retrieved chunk of document {did} is outside the turn")
        docs.append((doc, by_document[did]))
    return docs


def _outdated_documents(
    docs: list[tuple[Document, list[Chunk]]], snapshot: PolicySnapshot
) -> list[tuple[Document, list[Chunk]]]:
    """Keep the documents not masked under ``snapshot`` or not ``completed``."""
    return [
        (doc, doc_chunks)
        for doc, doc_chunks in docs
        if doc.ingestion_status != "completed"
        or document_staleness(doc, snapshot).needs_reprocessing
    ]


def _take_cached_remasks(
    doc: Document,
    doc_chunks: list[Chunk],
    snapshot: PolicySnapshot,
    doc_map: AnonymizationMap,
    restored: dict[int, str],
) -> list[tuple[Chunk, tuple]]:
    """Fill ``restored`` from :data:`remask_cache`; return the chunks it missed with their keys."""
    misses: list[tuple[Chunk, tuple]] = []
    language = effective_masking_language(doc)
    for c in doc_chunks:
        key = remask_cache.key(
            doc.id, c.id, snapshot.cache_key, c.sanitized_text or "", language
        )
        hit = remask_cache.get(key, doc_map)
        if hit is None:
            misses.append((c, key))
        else:
            restored[c.id] = hit
    return misses


async def _resanitize_outdated_chunks(
    db: AsyncSession,
    settings: AppSettings,
    chunks: Sequence[Chunk],
    documents_by_id: dict[int, Document],
) -> tuple[dict[int, str], set[int]]:
    """Re-mask chunks whose document was not sanitized under the current policy.

    Returns ``({chunk_id: text}, ids of documents whose map gained entities)``.
    Only chunks of outdated documents are in the dict; chunks of documents
    whose snapshot matches the current policy cost no sanitizer call. A
    document that is not ``completed`` is re-masked too: its snapshot does
    not describe the chunks it still has. Every chunk's document must be one
    of the turn's documents.

    A chunk already re-masked under the same policy is taken from
    :data:`remask_cache` while the document map still resolves it; the rest
    go through :func:`remask_document_texts` with the sanitizer the ingestion
    pipeline uses (same settings, same
    :data:`DOCUMENT_SANITIZER_ENABLE_OLLAMA`). Placeholders the document map
    already knows are never re-detected, and entities the current policy
    finds are added to the document's own (encrypted) map, so the turn's
    unified numbering, the de-anonymizer and a retry of the turn all resolve
    them. A document whose map is missing or erased mid-turn aborts the
    turn. The chunk rows are not rewritten; reprocessing the document is
    what brings them up to date.
    """
    if not chunks:
        return {}, set()
    docs = _chunks_by_turn_document(chunks, documents_by_id)

    # Cheap read first, so a turn over fresh documents builds no recognizer.
    if not _outdated_documents(docs, await current_policy_snapshot(db)):
        return {}, set()
    # The sanitizer, the staleness verdict and the cache key all come from
    # this one composition, so text masked under one policy is never cached
    # under another's key.
    policy, current = await PolicyComposer().compose_with_snapshot(db, settings)
    restored: dict[int, str] = {}
    changed: set[int] = set()
    sanitizer = None
    for doc, doc_chunks in _outdated_documents(docs, current):
        doc_map = await get_document_map(doc.id)
        if doc_map is None:
            raise DocumentMapMissingError(f"Document {doc.id} has no anonymization map")
        misses = _take_cached_remasks(doc, doc_chunks, current, doc_map, restored)
        if not misses:
            continue
        if sanitizer is None:
            sanitizer = await create_sanitizer(
                db, settings, enable_ollama=DOCUMENT_SANITIZER_ENABLE_OLLAMA, policy=policy
            )
        texts, written = await remask_document_texts(
            db, settings, doc, [c.sanitized_text or "" for c, _ in misses], sanitizer
        )
        if written:
            changed.add(doc.id)
        for (c, key), text in zip(misses, texts):
            restored[c.id] = text
            remask_cache.put(key, text)
    return restored, changed


_ENTITY_PLACEHOLDER_RE = re.compile(r"\[([A-Z][A-Z_]*)_(\d+)\]")


def _extract_entity_type(placeholder: str) -> str | None:
    """Return the entity type encoded in a placeholder like ``[PERSON_NAME_3]``.

    Mirrors the parser used by the entity index service. Kept as a
    private helper here so the chat router does not have to import the
    service-level regex into its own namespace.
    """
    if not placeholder:
        return None
    match = _ENTITY_PLACEHOLDER_RE.fullmatch(placeholder)
    return match.group(1) if match else None


def _build_unified_placeholder_space(
    anon_maps: dict[int, AnonymizationMap],
) -> tuple[dict[int, dict[str, str]], AnonymizationMap]:
    """Re-namespace per-document placeholders into one global set.

    Each document's anon_map is built with a doc-local counter, so two
    documents can both contain ``[ORGANIZATION_2]`` for different
    organizations, and one person can carry a different placeholder in
    each. Every per-map placeholder moves as a unit to exactly one global
    placeholder (see :func:`septum_core.placeholder_space.unify_placeholder_spaces`
    for when two maps share one), so a chunk keeps naming the person its
    own document meant. Returns:

    * ``per_doc_remap`` — for each map, the ``{old: new}`` rename to apply
      to its masked text; placeholders that keep their number are left out.
    * ``unified_map`` — the map the deanonymizer and the PII gate read for
      the turn; it is never persisted.
    """
    remaps, unified_map = unify_placeholder_spaces(list(anon_maps.items()))
    per_doc_remap = {
        key: {old: new for old, new in remap.items() if old != new}
        for key, remap in remaps.items()
    }
    return per_doc_remap, unified_map


def _apply_chunk_placeholder_remap(
    text: str, remap: dict[str, str]
) -> str:
    """Rewrite per-document placeholders in ``text`` using ``remap``.

    One regex pass over every bracketed run, each looked up in ``remap``:
    a match is touched exactly once, so swap-shaped renames
    (``{[PERSON_NAME_1]: [PERSON_NAME_2], [PERSON_NAME_2]: [PERSON_NAME_1]}``)
    stay swaps instead of cascading as sequential ``str.replace`` calls
    would, and ``[ORGANIZATION_10]`` is matched whole, never as
    ``[ORGANIZATION_1]`` plus a tail. Remap keys are placeholders, bracketed
    runs without inner brackets. The pattern does not depend on the remap,
    so the cost stays linear in the text however many entries it has.
    """
    if not text or not remap:
        return text
    return _BRACKETED_RUN.sub(lambda m: remap.get(m.group(0), m.group(0)), text)


_BRACKETED_RUN = re.compile(r"\[[^\[\]]*\]")




async def _unify_turn_placeholders(
    query_map: AnonymizationMap,
    document_ids: List[int],
    language: str,
) -> tuple[dict[int, AnonymizationMap], dict[int, dict[str, str]], dict[str, str], AnonymizationMap]:
    """Build the turn's unified placeholder space from the query and document maps.

    Each document map is (re)loaded from the store. The query map is folded
    in under a reserved key so an entity named in the question and in a
    document collapses onto one placeholder. The construction is
    deterministic for the same query map and document set, which is what
    lets a ``pre_approved_chunks`` retry rebuild the exact numbering the
    chunks were produced under. Documents are taken in id order so the
    result depends on the document set only, never on request order.

    Returns ``(doc_maps, per_doc_remap, query_remap, unified_map)``.
    """
    ordered_ids = sorted(set(document_ids))
    loaded = await asyncio.gather(*(get_document_map(did) for did in ordered_ids))
    doc_maps: dict[int, AnonymizationMap] = {
        did: (m or AnonymizationMap(document_id=did, language=language))
        for did, m in zip(ordered_ids, loaded)
    }
    per_doc_remap, unified_map = _build_unified_placeholder_space(
        {QUERY_MAP_KEY: query_map, **doc_maps}
    )
    query_remap = per_doc_remap.pop(QUERY_MAP_KEY, {})
    return doc_maps, per_doc_remap, query_remap, unified_map


_DOCUMENT_NOT_FOUND = "Document not found."
THEME_SNIPPET_LEN = 400
MIN_CHUNKS_FOR_LAST = 6
_DEFAULT_RELEVANCE_THRESHOLD = 0.35


def _effective_top_k(base_top_k: int, chunk_count: int) -> int:
    """Compute retrieval top_k from base and document chunk count.

    For longer documents, requests more chunks so holistic queries get
    sufficient context. Purely numeric; no language or document-type logic.
    """
    if chunk_count <= 0:
        return base_top_k
    bonus = 0 if chunk_count <= 10 else min(10, chunk_count // 3)
    return min(TOP_K_RETRIEVAL_MAX_CAP, max(base_top_k, base_top_k + bonus))


# Module-level singleton instances to avoid parallel model loading issues
_vector_store_singleton: Optional["VectorStore"] = None
_bm25_retriever_singleton: Optional["BM25Retriever"] = None

def _get_vector_store() -> "VectorStore":
    """Return the singleton VectorStore instance."""
    global _vector_store_singleton
    if _vector_store_singleton is None:
        from ..services.vector_store import VectorStore
        _vector_store_singleton = VectorStore()
    return _vector_store_singleton

def _get_bm25_retriever() -> "BM25Retriever":
    """Return the singleton BM25Retriever instance."""
    global _bm25_retriever_singleton
    if _bm25_retriever_singleton is None:
        from ..services.bm25_retriever import BM25Retriever
        _bm25_retriever_singleton = BM25Retriever()
    return _bm25_retriever_singleton


async def _classify_query_intent(
    query: str,
    document_names: list[str],
    ollama_base_url: str,
    ollama_model: str,
) -> str | None:
    """Classify whether a query requires document search or is casual chat.

    Uses the local Ollama model for privacy-preserving intent detection.
    Returns ``"search"``, ``"chat"``, or ``None`` if classification is
    unavailable (Ollama down / not configured).
    """
    if not ollama_base_url or not ollama_model:
        return None

    from ..services.ollama_client import call_ollama_async

    prompt = PromptCatalog.intent_classification(query, document_names)
    response = await call_ollama_async(
        prompt=prompt,
        base_url=ollama_base_url,
        model=ollama_model,
        timeout=5.0,
        options={"num_predict": 5, "temperature": 0.0},
    )
    if not response:
        return None
    return "chat" if "CHAT" in response.strip().upper() else "search"


async def _retrieve_chunks(
    db: AsyncSession,
    document_id: int,
    query: str,
    top_k: int,
    chunk_count: Optional[int] = None,
    relevance_threshold: float = _DEFAULT_RELEVANCE_THRESHOLD,
) -> List[Chunk]:
    """Retrieve relevance-filtered chunks using hybrid search (BM25 + FAISS).

    Combines keyword-based BM25 search with semantic FAISS search using
    Reciprocal Rank Fusion (RRF). When chunk_count > 1, also runs a
    document-theme search (first chunk snippet) and merges with RRF so
    holistic queries get both query-relevant and document-representative
    chunks.

    After the hybrid search the raw RRF scores are max-normalized to [0, 1]
    and any chunk below :data:`RELEVANCE_SCORE_THRESHOLD` is dropped. This
    gives the approval modal a question-shaped chunk set instead of a fixed
    ``top_k`` — a narrow question may return 2 chunks, a broad one 8.
    ``top_k`` is only an upper bound on how many candidates are fetched from
    hybrid_search; the filter decides how many of them actually survive.

    Falls back to the first ``top_k`` chunks by document order if the hybrid
    search returns nothing at all (e.g. the indexes are missing).
    """
    vector_store = _get_vector_store()
    bm25_retriever = _get_bm25_retriever()

    # Single hybrid search against the user's actual query. A previous
    # revision also fused a second "document theme" retrieval (first-chunk
    # snippet) via RRF to help holistic queries, but that systematically
    # pulled in any chunk "generally related" to the document, which made
    # the approval-modal chunk set basically question-independent on small
    # documents. The score-based relevance filter below relies on the
    # scores being meaningful *relative to the user's query*, so we run
    # one hybrid search and let the user's query speak for itself. If a
    # user wants holistic coverage, raising ``top_k_retrieval`` in Settings
    # is the proper knob.
    results = await asyncio.to_thread(
        vector_store.hybrid_search,
        document_id=document_id,
        query=query,
        top_k=top_k,
        bm25_retriever=bm25_retriever,
        alpha=0.5,
        beta=0.5,
    )

    if results:
        # Normalize the RRF fusion scores against the best match so the
        # filter has a consistent meaning across queries. RRF scores are
        # unbounded sums of 1/(rrf_k + rank) terms; comparing raw values
        # across different queries is meaningless, but a chunk that scores
        # X% of the current query's top result has a stable interpretation.
        max_score = max((score for _, score in results), default=0.0)
        if max_score <= 0:
            normalized = [(cid, 0.0) for cid, _ in results]
        else:
            normalized = [(cid, score / max_score) for cid, score in results]

        # Drop any candidate below the absolute relevance floor. The top
        # result (normalized score 1.0) always survives, so the filter can
        # shrink the count below ``top_k`` when the tail of the retrieval
        # is clearly unrelated, but never returns an empty set.
        filtered = [
            (cid, score)
            for cid, score in normalized
            if score >= relevance_threshold
        ]
        if not filtered:
            # Defensive: keep at least the top result so the caller never
            # has to deal with "retrieval returned nothing".
            filtered = normalized[:1]

        chunk_id_order = [cid for cid, _ in filtered]
        stmt = select(Chunk).where(Chunk.id.in_(chunk_id_order))
        db_result = await db.execute(stmt)
        base_chunks = db_result.scalars().all()
        # Preserve retrieval order (by normalized score desc) rather than
        # the DB's natural order so the highest-scoring chunks appear first
        # in the approval modal.
        order_index = {cid: idx for idx, cid in enumerate(chunk_id_order)}
        chunks = sorted(
            base_chunks, key=lambda c: order_index.get(c.id, len(chunk_id_order))
        )
    else:
        # Hybrid retrieval returned nothing at all (e.g. both indexes are
        # missing or the document was not yet indexed). Fall back to the
        # first ``top_k`` chunks by document order so the chat still has
        # some context to work with.
        fallback_stmt = (
            select(Chunk)
            .where(Chunk.document_id == document_id)
            .order_by(Chunk.index)
            .limit(top_k)
        )
        db_result = await db.execute(fallback_stmt)
        chunks = list(db_result.scalars().all())

    return chunks


async def _retrieve_chunks_all_documents(
    db: AsyncSession,
    query: str,
    top_k: int,
    documents: list[Document],
    relevance_threshold: float = _DEFAULT_RELEVANCE_THRESHOLD,
) -> tuple[List[Chunk], List[int]]:
    """Search ALL completed documents for the user and return relevant chunks.

    Returns ``(chunks, matched_document_ids)``.  If no chunk from any
    document survives the *relevance_threshold*, returns ``([], [])``.
    """
    if not documents:
        return [], []

    vector_store = _get_vector_store()
    all_docs = documents

    per_doc_k = max(5, min(50 // max(len(all_docs), 1), top_k))

    async def _search_one(doc: Document) -> list[tuple[int, float, int]]:
        # Use FAISS cosine similarity (not hybrid RRF) for auto-RAG.
        # Cosine similarity is absolute [0, 1] and comparable across
        # documents — a greeting like "Merhaba" scores <0.2 against
        # contract text, while a real question scores >0.35. RRF scores
        # are rank-based and always make the top result look relevant
        # after max-normalization, defeating the threshold filter.
        results = await asyncio.to_thread(
            vector_store.search, doc.id, query, top_k=per_doc_k
        )
        if not results:
            return []
        return [(cid, score, doc.id) for cid, score in results]

    per_doc_results = await asyncio.gather(
        *(_search_one(doc) for doc in all_docs)
    )

    all_scored: list[tuple[int, float, int]] = []
    for res in per_doc_results:
        all_scored.extend(res)

    if not all_scored:
        return [], []

    all_scored.sort(key=lambda x: x[1], reverse=True)

    filtered = [
        (cid, score, did)
        for cid, score, did in all_scored
        if score >= relevance_threshold
    ]
    if not filtered:
        return [], []

    cap = min(top_k * 3, MAX_CONTEXT_CHUNKS)
    filtered = filtered[:cap]

    chunk_ids = [cid for cid, _, _ in filtered]
    matched_doc_ids = sorted({did for _, _, did in filtered})

    stmt = select(Chunk).where(Chunk.id.in_(chunk_ids))
    db_result = await db.execute(stmt)
    base_chunks = db_result.scalars().all()

    order_index = {cid: idx for idx, (cid, _, _) in enumerate(filtered)}
    chunks = sorted(
        base_chunks, key=lambda c: order_index.get(c.id, len(filtered))
    )

    return chunks, matched_doc_ids


def _cap_context_chunks(chunks: List[Chunk], cap: int) -> List[Chunk]:
    """Keep at most ``cap`` chunks, taking them from each document in turn.

    Order within a document is preserved and documents keep their first
    appearance order, so a large multi-document selection is trimmed evenly
    instead of losing its last documents entirely.
    """
    if len(chunks) <= cap:
        return chunks
    per_doc: dict[int, List[Chunk]] = {}
    for c in chunks:
        per_doc.setdefault(c.document_id, []).append(c)
    round_robin = [
        c for layer in zip_longest(*per_doc.values()) for c in layer if c is not None
    ]
    kept = {id(c) for c in round_robin[: max(cap, 0)]}
    return [c for c in chunks if id(c) in kept]


def _build_approval_chunks(chunks: List[Chunk], texts: List[str]) -> List[ApprovalChunk]:
    """Convert database chunks into ApprovalChunk payloads using provided texts."""
    approval_chunks: List[ApprovalChunk] = []
    for c, text in zip(chunks, texts):
        approval_chunks.append(
            ApprovalChunk(
                id=c.id,
                document_id=c.document_id,
                text=text,
                source_page=c.source_page,
                source_slide=c.source_slide,
                source_sheet=c.source_sheet,
                source_timestamp_start=c.source_timestamp_start,
                source_timestamp_end=c.source_timestamp_end,
                section_title=c.section_title,
            )
        )
    return approval_chunks


# A placeholder in prompt or answer text: the classic shape, its short alias
# ("[PERSON_1]") or any custom entity type (``PLACEHOLDER_SHAPE``:
# "[MRN2_1]", "[Project code_1]"). The shape has groups, so read whole
# matches through ``_placeholders_in``, never ``findall``.
_PLACEHOLDER_PATTERN = re.compile(rf"\[[A-Za-z_]+\d+\]|{PLACEHOLDER_SHAPE.pattern}")


def _placeholders_in(text: str) -> List[str]:
    """Every placeholder-shaped run of ``text`` (see ``_PLACEHOLDER_PATTERN``), in order."""
    return [m.group(0) for m in _PLACEHOLDER_PATTERN.finditer(text)]


def _typed_query_placeholders(
    message_text: str,
    sanitized_query: str,
    query_remap: dict[str, str],
    known: AbstractSet[str] = frozenset(),
) -> List[str]:
    """Return the placeholders the user typed, renumbered as in ``sanitized_query``.

    Each typed placeholder is carried through the query remap of the
    unified numbering and kept only if the sanitized query still holds it.
    Only the standard shape (``PLACEHOLDER_PATTERN``) counts as typed on
    its own; any other bracketed text ("[Clause 4_2]") counts only when the
    turn's map holds it (``known``), so ordinary brackets never replace the
    answer.
    """
    renumbered: List[str] = []
    for typed in _placeholders_in(message_text or ""):
        placeholder = query_remap.get(typed, typed)
        if PLACEHOLDER_PATTERN.fullmatch(typed) or placeholder in known:
            renumbered.append(placeholder)
    return [p for p in renumbered if p in sanitized_query]


def _extract_placeholders_from_context(context_text: str) -> List[str]:
    """Return unique placeholders found in context, in order of first appearance."""
    if not context_text:
        return []
    seen: set[str] = set()
    ordered: List[str] = []
    for m in _PLACEHOLDER_PATTERN.finditer(context_text):
        token = m.group(0)
        if token not in seen:
            seen.add(token)
            ordered.append(token)
    return ordered


def _encode_sse(payload: dict[str, Any]) -> bytes:
    """Encode a JSON payload as a single SSE data event."""
    data = json.dumps(payload, ensure_ascii=False)
    return f"data: {data}\n\n".encode("utf-8")


def _stored_label(column: SpreadsheetColumn) -> str:
    """Return a stored semantic label stripped and cut to the label limit."""
    return (column.semantic_label or "").strip()[:MAX_SCHEMA_LABEL_CHARS]


async def _schema_column_descriptions(
    db: AsyncSession,
    document_id: Optional[int],
    anon_map: AnonymizationMap,
    settings: AppSettings,
) -> List[str]:
    """Return the spreadsheet column descriptions for the cloud prompt.

    Only the first ``MAX_SCHEMA_LABELED_COLUMNS`` columns, in index order, are
    described; a single summary entry states how many further columns are
    omitted. Semantic labels are gated against the turn's request-local
    ``anon_map`` before use, so a label stored before write-time gating existed
    (or any raw value in it) never reaches the cloud LLM unmasked. Only the
    labelled columns that are described are gated and shown by their label; the
    rest, like a column without one, are shown by their generated technical
    label, never the stored one.
    """
    if document_id is None:
        return []
    schema_result = await db.execute(
        select(SpreadsheetSchema)
        .options(selectinload(SpreadsheetSchema.columns))
        .where(SpreadsheetSchema.document_id == document_id)
    )
    schema = schema_result.scalar_one_or_none()
    if schema is None or not schema.columns:
        return []
    ordered = sorted(schema.columns, key=lambda c: c.index)
    described = ordered[:MAX_SCHEMA_LABELED_COLUMNS]
    labelled = [c for c in described if _stored_label(c)]
    gated: List[str] = []
    if labelled:
        gated = await gate_untrusted_texts(
            [_stored_label(c) for c in labelled], anon_map, db, settings
        )
    label_by_index = {c.index: label for c, label in zip(labelled, gated)}
    descriptions: List[str] = []
    for column in described:
        label = label_by_index.get(column.index)
        description = f"index {column.index} → {label or generated_technical_label(column.index)}"
        if column.is_numeric:
            description += " (numeric)"
        descriptions.append(description)
    omitted = len(ordered) - len(described)
    if omitted:
        descriptions.append(PromptCatalog.spreadsheet_schema_omitted_columns(omitted))
    return descriptions


def _sent_placeholders(
    sanitized_query: str,
    context_chunks: List[str],
    column_descriptions: List[str],
    regulation_names: List[str],
) -> set[str]:
    """Return the placeholders that this turn's data put into the prompt.

    Instruction text is excluded on purpose: the system instructions carry
    example placeholders, and an example must never become a placeholder
    the de-anonymizer is allowed to restore.
    """
    sources = [sanitized_query, *context_chunks, *column_descriptions, *regulation_names]
    return {p for text in sources if text for p in _placeholders_in(text)}


def _assemble_user_prompt(
    sanitized_query: str,
    context_chunks: List[str],
    regulation_names: List[str],
    language: str,
    output_mode: str = "chat",
    column_descriptions: Optional[List[str]] = None,
    query_has_placeholder: bool = False,
) -> str:
    """Build the full masked user prompt that will be sent to the cloud LLM.

    This is the exact string the cloud LLM receives as the ``user`` message:
    system-ish instructions from :class:`PromptCatalog`, the active regulation
    list, any spreadsheet-schema instruction the document contributes, the
    context chunks interpolated as ``Chunk N:\\n…`` blocks, and the user's
    sanitized question.

    The helper is shared between the approval gate (which shows this exact
    string in the approval modal so the user knows byte-for-byte what will
    be sent) and :func:`_run_llm_and_deanonymize` (which actually sends it).
    If the user edits chunks in the approval modal the preview endpoint
    re-runs this function with the edited chunks so the preview stays in
    sync with what will eventually hit the cloud. ``column_descriptions``
    must come from :func:`_schema_column_descriptions` (already gated).
    """
    schema_instruction = ""
    if column_descriptions:
        schema_instruction = PromptCatalog.spreadsheet_schema_instruction(
            list(column_descriptions)
        )

    context_text_for_placeholders = ""
    if context_chunks:
        lines_tmp: List[str] = []
        for idx, chunk in enumerate(context_chunks, start=1):
            lines_tmp.append(f"Chunk {idx}:\n{chunk}")
        context_text_for_placeholders = "\n\n".join(lines_tmp)

    placeholders_in_context = _extract_placeholders_from_context(
        context_text_for_placeholders
    )

    placeholder_list_str = ""
    if placeholders_in_context and query_has_placeholder:
        placeholder_list_str = PromptCatalog.placeholder_list_instruction(
            placeholders_in_context
        )

    payload = ChatContextPayload(
        sanitized_query=sanitized_query,
        context_chunks=context_chunks,
        regulation_names=regulation_names,
        language=language,
        schema_instruction=schema_instruction,
        placeholder_list_str=placeholder_list_str,
        output_mode=output_mode,
    )

    return build_chat_prompt(payload)


@dataclass(frozen=True)
class _LlmTurnInput:
    """The masked inputs and the identity of one turn's LLM call.

    Every text field is already masked: it is exactly what may cross the
    cloud boundary. ``session_id`` keys the diagnostic log lines and
    ``debug_id`` / ``debug_owner_user_id`` the in-memory debug record.
    """

    sanitized_query: str
    context_chunks: List[str]
    regulation_names: List[str]
    language: str
    output_mode: str = "chat"
    session_id: Optional[str] = None
    column_descriptions: Optional[List[str]] = None
    query_has_placeholder: bool = False
    query_placeholders: Optional[List[str]] = None
    rag_mode: str = "manual"
    debug_id: Optional[str] = None
    debug_owner_user_id: Optional[int] = None


def _cloud_failure_handler(
    db: AsyncSession,
    http_request: Optional[Request],
    used_ollama_fallback_ref: Optional[List[bool]],
) -> Optional[Callable[[str, dict[str, Any]], Awaitable[None]]]:
    """Return the router's ``on_cloud_failure`` callback, or ``None`` without a request.

    The callback flags the local fallback and records the failure in the
    error log; a failing error-log write is reported, never raised.
    """
    if http_request is None:
        return None

    async def on_cloud_failure(message: str, extra: dict[str, Any]) -> None:
        if used_ollama_fallback_ref is not None:
            used_ollama_fallback_ref[0] = True
        try:
            await log_backend_message(db, http_request, message, level="ERROR", extra=extra)
        except Exception as log_exc:
            report_error_log_failure(log_exc)

    return on_cloud_failure


def _turn_user_prompt(turn: _LlmTurnInput) -> str:
    """Return the masked user prompt the cloud LLM receives for ``turn``."""
    if turn.rag_mode == "none":
        return PromptCatalog.pure_chat_prompt(turn.sanitized_query)
    return _assemble_user_prompt(
        sanitized_query=turn.sanitized_query,
        context_chunks=turn.context_chunks,
        regulation_names=turn.regulation_names,
        language=turn.language,
        output_mode=turn.output_mode,
        column_descriptions=turn.column_descriptions,
        query_has_placeholder=turn.query_has_placeholder,
    )


def _context_placeholders(context_chunks: List[str]) -> List[str]:
    """Return the placeholders of the numbered context block, in order of first appearance."""
    context_text_for_placeholders = ""
    if context_chunks:
        lines_tmp: List[str] = []
        for idx, chunk in enumerate(context_chunks, start=1):
            lines_tmp.append(f"Chunk {idx}:\n{chunk}")
        context_text_for_placeholders = "\n\n".join(lines_tmp)
    return _extract_placeholders_from_context(context_text_for_placeholders)


def _is_unhelpful_answer(masked_answer: str) -> bool:
    """Return True for a refusal or a long bullet list (five or more bullets)."""
    if any(p in masked_answer.lower() for p in PromptCatalog.REFUSAL_PHRASES):
        return True
    bullet_lines = [
        line.strip()
        for line in masked_answer.splitlines()
        if line.strip() and line.strip().startswith(("•", "-", "*"))
    ]
    return len(bullet_lines) >= 5


def _shape_masked_answer(
    masked_answer: str, turn: _LlmTurnInput, placeholders_in_context: List[str]
) -> str:
    """Narrow the masked answer to placeholders when the question named some.

    For explicit placeholder questions the answer is always exactly the
    queried placeholder tokens, so no linked attribute (identifier,
    location, ...) beyond what was asked can leak. A placeholder question
    whose answer carries no placeholder and is unhelpful is replaced by the
    context placeholders as a bullet list.
    """
    if turn.query_placeholders:
        return " ".join(dict.fromkeys(turn.query_placeholders))
    if (
        placeholders_in_context
        and turn.query_has_placeholder
        and not _PLACEHOLDER_PATTERN.search(masked_answer)
        and _is_unhelpful_answer(masked_answer)
    ):
        return "\n".join(f"- {p}" for p in placeholders_in_context)
    return masked_answer


def _turn_sent_placeholders(turn: _LlmTurnInput) -> set[str]:
    """Return the placeholders ``turn``'s data put into the prompt (see :func:`_sent_placeholders`)."""
    if turn.rag_mode == "none":
        return _sent_placeholders(turn.sanitized_query, [], [], [])
    return _sent_placeholders(
        turn.sanitized_query,
        turn.context_chunks,
        list(turn.column_descriptions or []),
        turn.regulation_names,
    )


def _restorable_placeholders(anon_map: AnonymizationMap) -> set[str]:
    """Return every placeholder ``anon_map`` can restore."""
    if anon_map.placeholder_lookup:
        return set(anon_map.placeholder_lookup.keys())
    if anon_map.entity_map:
        return set(anon_map.entity_map.values())
    return set()


def _log_surviving_placeholders(
    session_id: Optional[str],
    settings: AppSettings,
    anon_map: AnonymizationMap,
    masked_answer: str,
    deanonymized: str,
) -> None:
    """Log placeholder shape only: which placeholders the answer held and which survived.

    Tells at a glance whether the LLM emitted a placeholder the unified map
    did not carry (the failure that leaks ``[PERSON_NAME_1]`` literals to
    the user). Never logs an original value: placeholders are read from the
    masked answer only, as the standard shape or one the map can restore,
    and a residual is one of them still present after restoration. The
    restored text is never pattern-matched, because an original restored
    inside brackets ("[Ayşe Demir_2]") would look like a placeholder.
    """
    restorable = _restorable_placeholders(anon_map)
    answer_placeholders = sorted(
        {
            p
            for p in _placeholders_in(masked_answer)
            if PLACEHOLDER_PATTERN.fullmatch(p) or p in restorable
        }
    )
    residual_placeholders = [p for p in answer_placeholders if p in deanonymized]
    if answer_placeholders or residual_placeholders:
        logger.info(
            "chat deanon session_id=%s strategy=%s map_size=%s "
            "answer_placeholders=%s residual=%s missing_in_map=%s",
            session_id,
            getattr(settings, "deanon_strategy", "simple"),
            len(restorable),
            answer_placeholders,
            residual_placeholders,
            [p for p in answer_placeholders if p not in restorable],
        )


async def _run_llm_and_deanonymize(
    db: AsyncSession,
    settings: AppSettings,
    anon_map: AnonymizationMap,
    turn: _LlmTurnInput,
    http_request: Optional[Request] = None,
    used_ollama_fallback_ref: Optional[List[bool]] = None,
) -> str:
    """Call the LLM and apply local de-anonymization.

    When ``turn.debug_id`` is given, the masked prompt and masked answer of
    this turn are kept in the in-memory debug store under that id.
    """
    llm = LLMRouter(settings)
    user_prompt = _turn_user_prompt(turn)
    # Kept for answer post-processing, which needs the placeholders that
    # were actually present in the context.
    placeholders_in_context = _context_placeholders(turn.context_chunks)

    messages = [
        {"role": "user", "content": user_prompt},
    ]

    masked_answer = await llm.complete(
        messages=messages,
        on_cloud_failure=_cloud_failure_handler(db, http_request, used_ollama_fallback_ref),
    )
    masked_answer = _shape_masked_answer(masked_answer, turn, placeholders_in_context)

    # Only placeholders this turn's data exposed to the LLM are restored;
    # any other placeholder in the answer stays literal.
    deanonymizer = Deanonymizer(settings=settings)
    deanonymized = deanonymizer.deanonymize(
        masked_answer, anon_map, allowed_placeholders=_turn_sent_placeholders(turn)
    )
    _log_surviving_placeholders(turn.session_id, settings, anon_map, masked_answer, deanonymized)

    normalizer = TextNormalizer()
    result = await normalizer.normalize(db, deanonymized)
    if turn.debug_id is not None:
        set_chat_debug_record(
            debug_id=turn.debug_id,
            owner_user_id=turn.debug_owner_user_id,
            masked_prompt=user_prompt,
            masked_answer=masked_answer,
        )
    if not anon_map.entity_map and _PLACEHOLDER_PATTERN.search(result):
        result += (
            "\n\n(Names and other values could not be loaded. "
            "Check the encryption key in Settings and re-upload the document.)"
        )
    return result


def query_language(message: str, document: Optional[Document]) -> str:
    """Detect a chat query's language, with the targeted document's language as context.

    Both chat endpoints call this so a short or ambiguous query resolves
    the same way: a confident detection wins, otherwise the document's
    language (a valid override first, see ``document_language``) is used
    when the query's script allows it. Without a document the detector's own fallbacks apply.
    """
    context = document_language(document) if document is not None else None
    return detect_language(message, context_language=context)


class AnalyzeQueryRequest(BaseModel):
    """Pre-flight payload for the disambiguation picker."""

    message: str = Field(max_length=MAX_CHAT_MESSAGE_CHARS)
    document_ids: Optional[List[DocumentId]] = Field(default=None, max_length=MAX_RETRY_DOCUMENTS)


class AnalyzeQueryCluster(BaseModel):
    """One disambiguation cluster — a connected group of candidate documents."""

    document_ids: List[int]
    document_filenames: List[str]
    score: float


class AnalyzeQueryResponse(BaseModel):
    """Reply describing how the entity router would scope this query.

    ``requires_disambiguation`` is true only when there are 2+ disjoint
    candidate clusters with at least medium-strength entity overlap; the
    frontend uses that flag to decide whether to surface the picker. When
    false, the chat ``/ask`` endpoint is safe to call directly and will
    apply the same narrowing automatically.
    """

    requires_disambiguation: bool
    clusters: List[AnalyzeQueryCluster]
    narrowed_doc_ids: List[int]
    reason: str


@router.post(
    "/analyze_query",
    status_code=status.HTTP_200_OK,
)
async def chat_analyze_query(
    body: AnalyzeQueryRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AnalyzeQueryResponse:
    """Pre-flight: sanitize the user's question, run entity-aware
    routing, and group the matching documents into clusters so the
    frontend can show a disambiguation picker when several distinct
    real-world entities match the query."""

    settings = await load_settings(db)
    target_document = (
        await get_owned_or_404(
            db, Document, body.document_ids[0], user, _DOCUMENT_NOT_FOUND
        )
        if body.document_ids
        else None
    )
    language = query_language(body.message, target_document)
    anon_map = AnonymizationMap(document_id=0, language=language)
    await _sanitize_query(body.message, language, settings, anon_map, db)

    query_entities: List[Tuple[str, str]] = []
    for original, placeholder in anon_map.entity_map.items():
        entity_type = _extract_entity_type(placeholder)
        if entity_type and original:
            query_entities.append((original, entity_type))

    if not query_entities:
        return AnalyzeQueryResponse(
            requires_disambiguation=False,
            clusters=[],
            narrowed_doc_ids=[],
            reason="no_entity_in_query",
        )

    scores = await find_documents_for_query_entities(
        db,
        query_entities,
        owner_id=None if user.role == "admin" else user.id,
    )
    if scores:
        # Same scope as auto-RAG in ``/ask``: the picker must never list a
        # document the chat turn would then refuse to retrieve from.
        visible = await db.execute(
            select(Document.id).where(
                Document.id.in_(list(scores)), _auto_rag_owner_filter(user)
            )
        )
        visible_ids = set(visible.scalars().all())
        scores = {did: sc for did, sc in scores.items() if did in visible_ids}
    if not scores:
        return AnalyzeQueryResponse(
            requires_disambiguation=False,
            clusters=[],
            narrowed_doc_ids=[],
            reason="no_entity_match",
        )

    medium_or_better = {
        did for did, sc in scores.items() if sc >= RELATIONSHIP_THRESHOLD_MEDIUM
    }
    if not medium_or_better:
        return AnalyzeQueryResponse(
            requires_disambiguation=False,
            clusters=[],
            narrowed_doc_ids=[],
            reason="weak_entity_match_only",
        )

    cluster_lists = await cluster_documents_by_relationship(
        db, list(medium_or_better)
    )
    docs_q = await db.execute(
        select(Document.id, Document.original_filename).where(
            Document.id.in_(medium_or_better)
        )
    )
    name_by_id = {
        doc_id: filename or f"document_{doc_id}"
        for doc_id, filename in docs_q.all()
    }

    cluster_payload = [
        AnalyzeQueryCluster(
            document_ids=ids,
            document_filenames=[name_by_id.get(d, str(d)) for d in ids],
            score=max(scores.get(d, 0.0) for d in ids),
        )
        for ids in cluster_lists
    ]
    cluster_payload.sort(key=lambda c: c.score, reverse=True)

    requires = len(cluster_lists) > 1
    if requires:
        narrowed = [d for ids in cluster_lists for d in ids]
    elif cluster_lists:
        narrowed = cluster_lists[0]
    else:
        narrowed = []

    return AnalyzeQueryResponse(
        requires_disambiguation=requires,
        clusters=cluster_payload,
        narrowed_doc_ids=narrowed,
        reason="multi_cluster_ambiguity" if requires else "single_cluster",
    )


_MIN_CHUNKS_PER_DOCUMENT = 10
_ENTITY_MATCH_REASONS = (
    "strong_entity_match",
    "medium_entity_match",
    "weak_entity_match_only",
)
_UNEXPECTED_ERROR_MESSAGE = "An unexpected error occurred while processing the chat request."


@dataclass
class _ChatTurn:
    """Mutable state of one ``/ask`` turn, handed from phase to phase.

    ``request`` and ``message_text`` carry the user's raw message;
    ``sanitized_query`` and the other prompt fields hold masked text once
    the query is sanitized. ``anon_map`` is the turn's request-local map: it
    never leaves the process except inside the approval session, and it is
    never logged. The raw fields and the map are left out of ``repr``.
    """

    request: ChatRequest = field(repr=False)
    message_text: str = field(repr=False)
    user: User
    session_id: str
    debug_id: str
    settings: AppSettings
    active_regulations: List[Tuple[str, bool]]
    documents_by_id: dict[int, Document]
    document: Optional[Document]
    language: str
    query_seed_document_id: Optional[int]
    anon_map: AnonymizationMap = field(repr=False)
    rag_mode: str
    sanitized_query: str = ""
    entity_count: int = 0
    query_type_counts: dict[str, int] = field(default_factory=dict)
    matched_document_ids: List[int] = field(default_factory=list)
    matched_document_names: List[str] = field(default_factory=list)
    chunks: List[Chunk] = field(default_factory=list)
    query_remap: dict[str, str] = field(default_factory=dict)
    numbering_fp: bytes = b""
    candidate_chunks: List[ApprovalChunk] = field(default_factory=list)
    output_mode: str = "chat"
    query_placeholders: List[str] = field(default_factory=list)
    context_texts: List[str] = field(default_factory=list)
    prompt_regulation_names: List[str] = field(default_factory=list)
    column_descriptions: List[str] = field(default_factory=list)
    finished: bool = False

    @property
    def regulation_names(self) -> List[str]:
        """Display names of the active rulesets, as configured."""
        return [name for name, _ in self.active_regulations]

    @property
    def query_has_placeholder(self) -> bool:
        """Whether the user typed a placeholder the sanitized query still holds."""
        return bool(self.query_placeholders)


async def _initial_query_map(
    document: Optional[Document], language: str, settings: AppSettings
) -> AnonymizationMap:
    """Return the map the query is sanitized against, seeded from ``document``'s map.

    The query is sanitized against a copy: the cached map object is shared
    by every request and must never gain query PII.
    """
    deanon_enabled = getattr(settings, "deanon_enabled", True)
    if document is None:
        anon_map = AnonymizationMap(document_id=0, language=language)
        logger.info(
            "chat document_id=None anon_map_entries=%s deanon_enabled=%s",
            len(anon_map.entity_map),
            deanon_enabled,
        )
        return anon_map
    cached_map = await get_document_map(document.id)
    anon_map = (
        copy.deepcopy(cached_map)
        if cached_map is not None
        else AnonymizationMap(document_id=document.id, language=language)
    )
    map_entries = len(anon_map.entity_map)
    logger.info(
        "chat document_id=%s anon_map_entries=%s deanon_enabled=%s",
        document.id,
        map_entries,
        deanon_enabled,
    )
    if map_entries == 0 and deanon_enabled:
        logger.warning(
            "Deanonymization will have no effect: no map for document_id=%s. "
            "Encryption key may have changed — re-upload the document so the map can be loaded.",
            document.id,
        )
    return anon_map


async def _open_turn(
    request: ChatRequest,
    message_text: str,
    document_ids_list: List[int],
    requested_documents: dict[int, Document],
    user: User,
    session_id: str,
    debug_id: str,
    db: AsyncSession,
) -> _ChatTurn:
    """Load settings and rulesets, pick the lead document and seed the query map."""
    settings = await load_settings(db)
    active_regulations = await _active_regulations(db)
    documents_by_id: dict[int, Document] = dict(requested_documents)
    document_id = document_ids_list[0] if document_ids_list else None
    document = documents_by_id.get(document_id) if document_id is not None else None
    language = query_language(message_text, document)
    anon_map = await _initial_query_map(document, language, settings)
    return _ChatTurn(
        request=request,
        message_text=message_text,
        user=user,
        session_id=session_id,
        debug_id=debug_id,
        settings=settings,
        active_regulations=active_regulations,
        documents_by_id=documents_by_id,
        document=document,
        language=language,
        # The document whose map seeds the query map; part of the
        # numbering fingerprint a retry is checked against.
        query_seed_document_id=document.id if document is not None else None,
        anon_map=anon_map,
        rag_mode="manual" if document_ids_list else "auto",
    )


async def _sanitize_turn_query(turn: _ChatTurn, db: AsyncSession) -> None:
    """Mask the user's message against the turn's query map."""
    async with _phase_timer(turn.session_id, "sanitize_query"):
        turn.sanitized_query, turn.entity_count, turn.query_type_counts = (
            await _sanitize_query(
                turn.message_text, turn.language, turn.settings, turn.anon_map, db
            )
        )


def _per_document_top_k(num_docs: int, top_k: int) -> int:
    """Return how many chunks to fetch from each of ``num_docs`` documents."""
    return max(
        _MIN_CHUNKS_PER_DOCUMENT,
        min(MAX_CONTEXT_CHUNKS // num_docs, top_k // num_docs),
    )


async def _retrieve_manual_chunks(turn: _ChatTurn, db: AsyncSession) -> None:
    """Retrieve the chunks of the documents the user picked."""
    documents = turn.documents_by_id
    relevance_threshold = turn.settings.rag_relevance_threshold
    async with _phase_timer(turn.session_id, "retrieve_chunks"):
        base_top_k = turn.request.top_k or turn.settings.top_k_retrieval
        total_chunk_count = sum(d.chunk_count for d in documents.values())
        top_k = _effective_top_k(base_top_k, total_chunk_count)
        if len(documents) == 1:
            doc = next(iter(documents.values()))
            chunks = await _retrieve_chunks(
                db=db,
                document_id=doc.id,
                query=turn.sanitized_query,
                top_k=base_top_k,
                chunk_count=doc.chunk_count,
                relevance_threshold=relevance_threshold,
            )
        else:
            per_doc_k = _per_document_top_k(len(documents), top_k)
            chunks = []
            for doc in documents.values():
                chunks_doc = await _retrieve_chunks(
                    db=db,
                    document_id=doc.id,
                    query=turn.sanitized_query,
                    top_k=per_doc_k,
                    chunk_count=doc.chunk_count,
                    relevance_threshold=relevance_threshold,
                )
                chunks.extend(chunks_doc)
        # Every offer must fit the retry limit, so a rejected
        # turn can always be sent again.
        turn.chunks = _cap_context_chunks(chunks, MAX_CONTEXT_CHUNKS)


def _adopt_retry_documents(turn: _ChatTurn, retry_documents: dict[int, Document]) -> None:
    """Join the retry chunks' documents to the turn.

    Same document set (and lead document) the original turn unified its
    placeholders over, so the retry rebuilds the identical numbering; in
    auto mode no ids were requested.
    """
    for did, doc in retry_documents.items():
        turn.documents_by_id.setdefault(did, doc)
    if turn.document is None:
        turn.document = next(iter(turn.documents_by_id.values()))


def _scope_to_choice(turn: _ChatTurn, docs: List[Document]) -> List[Document]:
    """Keep the documents the disambiguation picker chose.

    The picker's set (sent back via ``analyze_query``) is honoured verbatim
    and the entity-aware narrowing pass is skipped.
    """
    scope = set(turn.request.scoped_doc_ids or [])
    before = len(docs)
    scoped = [d for d in docs if d.id in scope]
    logger.info(
        "chat entity-routing session_id=%s reason=disambiguation_choice "
        "candidates=%d narrowed=%d",
        turn.session_id,
        before,
        len(scoped),
    )
    return scoped


def _query_entities(anon_map: AnonymizationMap) -> List[Tuple[str, str]]:
    """Return ``(original, entity_type)`` for every typed entity the query map holds."""
    query_entities = [
        (original, _extract_entity_type(placeholder))
        for original, placeholder in anon_map.entity_map.items()
        if placeholder
    ]
    return [(val, etype) for val, etype in query_entities if etype]


async def _entity_scoped_documents(
    db: AsyncSession, query_entities: List[Tuple[str, str]], user: User
) -> tuple[set[int], str]:
    """Return the documents the query's entities point to, with the reason.

    Only a strong or medium match narrows; a weak one (e.g. a shared
    location) is too noisy to scope on alone.
    """
    entity_scores = await find_documents_for_query_entities(
        db,
        query_entities,
        owner_id=None if user.role == "admin" else user.id,
    )
    if not entity_scores:
        return set(), "no_entity_in_query"
    strong = {did for did, sc in entity_scores.items() if sc >= RELATIONSHIP_THRESHOLD_STRONG}
    if strong:
        return strong, "strong_entity_match"
    medium = {did for did, sc in entity_scores.items() if sc >= RELATIONSHIP_THRESHOLD_MEDIUM}
    if medium:
        return medium, "medium_entity_match"
    return set(), "weak_entity_match_only"


async def _narrow_by_query_entities(
    turn: _ChatTurn, db: AsyncSession, docs: List[Document]
) -> tuple[List[Document], str]:
    """Scope the candidates to the documents holding the PII the query names.

    Mixing chunks across unrelated documents risks the cloud LLM
    attributing one person's data to another. Without a usable entity
    match every document stays a candidate. The match is HMAC-keyed under
    the local encryption key, so no original reaches the index lookup.
    """
    query_entities = _query_entities(turn.anon_map)
    narrowed_doc_ids: set[int] = set()
    narrowing_reason = "no_entity_in_query"
    if query_entities:
        narrowed_doc_ids, narrowing_reason = await _entity_scoped_documents(
            db, query_entities, turn.user
        )
    if narrowed_doc_ids:
        before = len(docs)
        docs = [d for d in docs if d.id in narrowed_doc_ids]
        logger.info(
            "chat entity-routing session_id=%s reason=%s "
            "candidates=%d narrowed=%d",
            turn.session_id,
            narrowing_reason,
            before,
            len(docs),
        )
    return docs, narrowing_reason


async def _classify_unless_entity_match(
    turn: _ChatTurn, docs: List[Document], narrowing_reason: str
) -> None:
    """Switch the turn to pure chat when the intent classifier says so.

    Any entity match (strong, medium or weak) is hard evidence the question
    is about the documents, so the non-deterministic classifier is only
    consulted when the query names no indexed entity.
    """
    if narrowing_reason in _ENTITY_MATCH_REASONS:
        logger.info(
            "chat intent-classifier session_id=%s skipped reason=%s",
            turn.session_id,
            narrowing_reason,
        )
        return
    doc_names = [d.original_filename or f"document_{d.id}" for d in docs]
    intent = await _classify_query_intent(
        query=turn.sanitized_query,
        document_names=doc_names,
        ollama_base_url=turn.settings.ollama_base_url,
        ollama_model=turn.settings.ollama_chat_model,
    )
    if intent == "chat":
        turn.rag_mode = "none"


async def _auto_rag_candidates(turn: _ChatTurn, db: AsyncSession) -> List[Document]:
    """Return the documents auto-RAG may retrieve from; may switch the turn to pure chat."""
    all_user_docs_result = await db.execute(
        select(Document).where(
            _auto_rag_owner_filter(turn.user),
            Document.ingestion_status == "completed",
            Document.chunk_count > 0,
        )
    )
    all_user_docs = list(all_user_docs_result.scalars().all())
    if not all_user_docs:
        turn.rag_mode = "none"
        return all_user_docs
    if turn.request.scoped_doc_ids:
        return _scope_to_choice(turn, all_user_docs)
    all_user_docs, narrowing_reason = await _narrow_by_query_entities(turn, db, all_user_docs)
    await _classify_unless_entity_match(turn, all_user_docs, narrowing_reason)
    return all_user_docs


async def _retrieve_auto_chunks(
    turn: _ChatTurn, db: AsyncSession, candidates: List[Document]
) -> None:
    """Retrieve across the candidates; a turn with no relevant chunk becomes pure chat."""
    async with _phase_timer(turn.session_id, "retrieve_chunks_auto"):
        base_top_k = turn.request.top_k or turn.settings.top_k_retrieval
        turn.chunks, turn.matched_document_ids = await _retrieve_chunks_all_documents(
            db=db,
            query=turn.sanitized_query,
            top_k=base_top_k,
            documents=candidates,
            relevance_threshold=turn.settings.rag_relevance_threshold,
        )
        if not turn.chunks:
            turn.rag_mode = "none"
            return
        docs_by_id = {d.id: d for d in candidates}
        for did in turn.matched_document_ids:
            doc_obj = docs_by_id.get(did)
            if doc_obj:
                turn.documents_by_id[did] = doc_obj
                turn.matched_document_names.append(doc_obj.original_filename or str(did))
        turn.document = next(iter(turn.documents_by_id.values()), None)


async def _run_auto_rag(turn: _ChatTurn, db: AsyncSession) -> None:
    """Pick the turn's context when the user named no document."""
    async with _phase_timer(turn.session_id, "auto_rag_classify"):
        candidates = await _auto_rag_candidates(turn, db)
    if turn.rag_mode == "auto":
        await _retrieve_auto_chunks(turn, db, candidates)


async def _select_turn_context(
    turn: _ChatTurn, db: AsyncSession, retry_documents: dict[int, Document]
) -> None:
    """Gather the turn's chunks (fresh turn) or documents (retry)."""
    is_retry = turn.request.pre_approved_chunks is not None
    if not is_retry and turn.rag_mode == "manual" and turn.documents_by_id:
        await _retrieve_manual_chunks(turn, db)
    elif is_retry and retry_documents:
        _adopt_retry_documents(turn, retry_documents)
    elif not is_retry and turn.rag_mode == "auto":
        await _run_auto_rag(turn, db)


async def _audit_query_pii(turn: _ChatTurn, db: AsyncSession) -> None:
    """Record the PII found in the query (counts only) in the audit trail."""
    if turn.entity_count <= 0:
        return
    document = turn.document
    await log_pii_detected(
        db,
        document_id=document.id if document else 0,
        regulation_ids=list(document.active_regulation_ids) if document else [],
        entity_type_counts=turn.query_type_counts,
        total_count=turn.entity_count,
        session_id=turn.session_id,
        extra={"source": "chat_query"},
    )


def _effective_settings(settings: AppSettings, request: ChatRequest) -> AppSettings:
    """Return ``settings`` with the request's de-anonymization override applied to a copy."""
    if request.deanon_enabled is None:
        return settings
    effective = copy.copy(settings)
    effective.deanon_enabled = request.deanon_enabled
    return effective


def _document_display_name(documents_by_id: dict[int, Document], did: int) -> str:
    """Return a document's original filename, or its id when it has none."""
    doc = documents_by_id.get(did)
    if doc is not None and doc.original_filename:
        return doc.original_filename
    return str(did)


def _matched_documents(turn: _ChatTurn) -> List[dict[str, Any]]:
    """Return the per-document chunk counts for the ``meta`` event, largest first."""
    chunks_per_doc: dict[int, int] = {}
    for c in turn.chunks:
        chunks_per_doc[c.document_id] = chunks_per_doc.get(c.document_id, 0) + 1
    return [
        {
            "id": did,
            "name": _document_display_name(turn.documents_by_id, did),
            "chunk_count": count,
        }
        for did, count in sorted(chunks_per_doc.items(), key=lambda kv: -kv[1])
    ]


def _meta_event(turn: _ChatTurn, require_approval: bool) -> dict[str, Any]:
    """Return the turn's opening ``meta`` event."""
    return {
        "type": "meta",
        "session_id": turn.session_id,
        "debug_id": turn.debug_id,
        "document_id": turn.document.id if turn.document is not None else None,
        "language": turn.language,
        "require_approval": require_approval,
        "retrieved_chunk_count": len(turn.chunks),
        "active_regulations": turn.regulation_names,
        "rag_mode": turn.rag_mode,
        "matched_document_ids": turn.matched_document_ids,
        "matched_document_names": turn.matched_document_names,
        "matched_documents": _matched_documents(turn),
    }


async def _prepare_retry_context(turn: _ChatTurn, db: AsyncSession) -> Optional[dict[str, Any]]:
    """Rebuild the retried turn's numbering and gate the client's chunks.

    Returns the error event to send when the rebuilt numbering does not
    match the one the chunks were produced under, else ``None``.
    """
    request = turn.request
    pre_approved = request.pre_approved_chunks or []
    client_texts = [c.text for c in pre_approved]
    query_map = turn.anon_map
    doc_maps: dict[int, AnonymizationMap] = {}
    if turn.documents_by_id:
        # The client returns chunks numbered in the unified space of the
        # turn that produced them; rebuild that same space so every
        # placeholder resolves to the entity it meant.
        doc_maps, _, turn.query_remap, turn.anon_map = await _unify_turn_placeholders(
            query_map, list(turn.documents_by_id.keys()), turn.language
        )
        _settle_query_strays(turn, query_map)
    turn.numbering_fp = numbering_fingerprint(query_map, doc_maps, turn.query_seed_document_id)
    # A rebuilt numbering is only trusted when it matches the one the chunks
    # were produced under (maps, selection and query unchanged since);
    # otherwise nothing is sent.
    if (doc_maps or request.numbering_token is not None) and not verify_numbering(
        request.numbering_token, turn.numbering_fp, turn.user.id
    ):
        logger.warning(
            "chat retry refused session_id=%s reason=%s docs=%s",
            turn.session_id,
            RETRY_NUMBERING_CHANGED,
            sorted(doc_maps),
        )
        return {
            "type": "error",
            "code": RETRY_NUMBERING_CHANGED,
            "message": (
                "The documents changed since this answer was "
                "prepared; please ask again."
            ),
        }
    if turn.query_remap:
        turn.sanitized_query = _apply_chunk_placeholder_remap(turn.sanitized_query, turn.query_remap)
    # Entities first seen in the client text must not reuse a number the
    # client text already carries for someone else.
    reserve_placeholder_counters(turn.anon_map, client_texts)
    client_texts = await gate_untrusted_texts(client_texts, turn.anon_map, db, turn.settings)
    turn.candidate_chunks = [
        ApprovalChunk(id=None, document_id=c.document_id, text=text)
        for c, text in zip(pre_approved, client_texts)
    ]
    return None


async def _reseed_query_map(turn: _ChatTurn, db: AsyncSession) -> None:
    """Seed and sanitize the query again from the seed document's grown map.

    The query map was seeded before the re-mask grew the seed document's
    map; a retry seeds from the grown map, so without this the retry's
    numbering would never match.
    """
    seed_map = await get_document_map(turn.query_seed_document_id)
    if seed_map is None:
        raise DocumentMapMissingError(
            f"Document {turn.query_seed_document_id} has no anonymization map"
        )
    turn.anon_map = copy.deepcopy(seed_map)
    turn.sanitized_query, turn.entity_count, _ = await _sanitize_query(
        turn.message_text, turn.language, turn.settings, turn.anon_map, db
    )


def _settle_query_strays(turn: _ChatTurn, query_map: AnonymizationMap) -> None:
    """Keep placeholders the query map does not hold (typed by the user) off every global.

    Runs right after the turn's space is built, before any chunk, so a retry
    settles them on the same placeholders.
    """
    settle_strays(turn.sanitized_query, turn.query_remap, map_placeholders(query_map), turn.anon_map)


async def _unify_fresh_turn(
    turn: _ChatTurn,
) -> tuple[dict[int, AnonymizationMap], dict[int, dict[str, str]]]:
    """Unify the query map with every turn document's reloaded map.

    The query map may be the empty map auto-RAG started with, and query
    entities must share the document's placeholder so the LLM sees one
    entity. A turn without chunks is unified too, so its retry rebuilds the
    same numbering. Returns ``(document maps, per-document remap)``.
    """
    query_anon_map = turn.anon_map
    anon_maps, per_doc_remap, turn.query_remap, turn.anon_map = await _unify_turn_placeholders(
        query_anon_map, list(turn.documents_by_id.keys()), turn.language
    )
    turn.numbering_fp = numbering_fingerprint(
        query_anon_map, anon_maps, turn.query_seed_document_id
    )
    _settle_query_strays(turn, query_anon_map)
    if turn.query_remap:
        turn.sanitized_query = _apply_chunk_placeholder_remap(turn.sanitized_query, turn.query_remap)

    # Log placeholder shape only — never the originals — so we can diagnose
    # unification gaps without writing PII to the local log file.
    logger.info(
        "chat unified-map session_id=%s docs=%s "
        "unified_entries=%s query_entries=%s "
        "query_remap_size=%s placeholders=%s",
        turn.session_id,
        sorted(anon_maps.keys()),
        len(turn.anon_map.entity_map),
        len(query_anon_map.entity_map),
        len(turn.query_remap),
        sorted(set(turn.anon_map.entity_map.values()))[:20],
    )
    return anon_maps, per_doc_remap


def _masked_chunk_text(
    chunk: Chunk,
    restored_chunk_texts: dict[int, str],
    chunk_map: AnonymizationMap,
    chunk_remap: Optional[dict[str, str]],
    strays: Optional[Tuple[set[str], AnonymizationMap]] = None,
) -> str:
    """Return one chunk's text masked by its map and renumbered into the turn's space.

    ``strays`` carries the placeholders the chunk's map holds and the turn's
    unified map; a placeholder in the text outside that set is settled off
    every global (see :func:`settle_strays`) into ``chunk_remap``.
    """
    chunk_text = restored_chunk_texts.get(chunk.id, chunk.sanitized_text or "")
    # Chunks were analysed at ingestion; the map is the source of truth here.
    masked_chunk = mask_known_entities(chunk_text, chunk_map)
    if strays is not None and chunk_remap is not None:
        settle_strays(masked_chunk, chunk_remap, *strays)
    if chunk_remap:
        masked_chunk = _apply_chunk_placeholder_remap(masked_chunk, chunk_remap)
    return masked_chunk


def _masked_chunk_texts(
    turn: _ChatTurn,
    restored_chunk_texts: dict[int, str],
    anon_maps: dict[int, AnonymizationMap],
    per_doc_remap: dict[int, dict[str, str]],
) -> List[str]:
    """Return the turn's chunk texts as the cloud LLM sees them.

    Documents are labelled by position, never by filename: an original
    filename routinely carries personal data and must not reach the cloud
    LLM.
    """
    multi_document = len(turn.documents_by_id) > 1
    document_labels: dict[int, int] = {}
    prev_doc_id: Optional[int] = None
    known: dict[int, set[str]] = {}
    texts: List[str] = []
    for c in turn.chunks:
        header = ""
        if multi_document and c.document_id != prev_doc_id:
            label = document_labels.setdefault(c.document_id, len(document_labels) + 1)
            header = PromptCatalog.document_context_header(label)
            prev_doc_id = c.document_id
        doc_map = anon_maps.get(c.document_id)
        strays = None
        if doc_map is not None:
            if c.document_id not in known:
                known[c.document_id] = map_placeholders(doc_map)
            strays = (known[c.document_id], turn.anon_map)
        masked_chunk = _masked_chunk_text(
            c,
            restored_chunk_texts,
            doc_map or turn.anon_map,
            per_doc_remap.setdefault(c.document_id, {}) if doc_map is not None else None,
            strays,
        )
        texts.append(header + masked_chunk)
    return texts


async def _prepare_fresh_context(turn: _ChatTurn, db: AsyncSession) -> None:
    """Mask the retrieved chunks in the turn's unified placeholder space."""
    turn.numbering_fp = numbering_fingerprint(turn.anon_map, {}, turn.query_seed_document_id)
    if not turn.documents_by_id:
        return
    # Before the maps are loaded: entities the current policy finds in
    # outdated chunks join their document's map.
    async with _phase_timer(turn.session_id, "resanitize_outdated_chunks"):
        restored_chunk_texts, remasked_doc_ids = await _resanitize_outdated_chunks(
            db, turn.settings, turn.chunks, turn.documents_by_id
        )
    if turn.query_seed_document_id in remasked_doc_ids:
        await _reseed_query_map(turn, db)
    anon_maps, per_doc_remap = await _unify_fresh_turn(turn)
    sanitized_chunk_texts = _masked_chunk_texts(
        turn, restored_chunk_texts, anon_maps, per_doc_remap
    )
    if turn.chunks:
        turn.candidate_chunks = _build_approval_chunks(turn.chunks, sanitized_chunk_texts)


async def _mask_turn_context(turn: _ChatTurn, db: AsyncSession) -> Optional[dict[str, Any]]:
    """Put the turn's context into its unified numbering; return an error event to stop."""
    if turn.request.pre_approved_chunks is not None:
        return await _prepare_retry_context(turn, db)
    await _prepare_fresh_context(turn, db)
    return None


async def _prepare_prompt_inputs(turn: _ChatTurn, db: AsyncSession) -> None:
    """Compute the prompt inputs the approval preview and the LLM call share.

    The approval modal's assembled-prompt preview uses exactly the
    parameters the real LLM call will use.
    """
    turn.output_mode = (turn.request.output_mode or "chat").strip().lower()
    # Placeholders the user typed, in the numbering the LLM sees: the
    # forced answer and the de-anonymization allow-set both read
    # ``sanitized_query``, so they must agree on every number.
    turn.query_placeholders = _typed_query_placeholders(
        turn.message_text,
        turn.sanitized_query,
        turn.query_remap,
        map_placeholders(turn.anon_map) | set(turn.anon_map.placeholder_lookup),
    )
    turn.context_texts = [c.text for c in turn.candidate_chunks]
    if turn.rag_mode == "none":
        turn.prompt_regulation_names = turn.regulation_names
        turn.column_descriptions = []
        return
    turn.prompt_regulation_names = await _prompt_regulation_names(
        turn.active_regulations, turn.anon_map, db, turn.settings
    )
    column_document_id = turn.document.id if turn.document is not None else None
    turn.column_descriptions = await _schema_column_descriptions(
        db, column_document_id, turn.anon_map, turn.settings
    )


def _approval_required_event(turn: _ChatTurn, assembled_prompt: str) -> dict[str, Any]:
    """Return the ``approval_required`` event offering the turn's masked context."""
    return {
        "type": "approval_required",
        "session_id": turn.session_id,
        "numbering_token": sign_numbering(turn.numbering_fp, turn.user.id),
        "masked_prompt": turn.sanitized_query,
        "assembled_prompt": assembled_prompt,
        "chunks": [
            {
                "id": c.id,
                "document_id": c.document_id,
                "text": c.text,
                "source_page": c.source_page,
                "section_title": c.section_title,
            }
            for c in turn.candidate_chunks
        ],
    }


async def _approval_events(
    turn: _ChatTurn, gate: ApprovalGate, db: AsyncSession
) -> AsyncGenerator[bytes, None]:
    """Run the approval gate; sets ``turn.finished`` when the turn must stop.

    On approval the chunk texts the user returned replace the offered ones,
    edited or added texts gated first.
    """
    # The exact masked prompt the cloud LLM would receive right now, so
    # the approval modal can show it byte-for-byte. Edits in the modal go
    # through a dedicated preview endpoint that re-runs the assembly.
    assembled_prompt = _assemble_user_prompt(
        sanitized_query=turn.sanitized_query,
        context_chunks=[c.text for c in turn.candidate_chunks],
        regulation_names=turn.prompt_regulation_names,
        language=turn.language,
        output_mode=turn.output_mode,
        column_descriptions=turn.column_descriptions,
        query_has_placeholder=turn.query_has_placeholder,
    )
    try:
        approval_session = await gate.create(
            session_id=turn.session_id,
            masked_prompt=turn.sanitized_query,
            masked_chunks=[c.text for c in turn.candidate_chunks],
            entity_count=turn.entity_count,
            owner_user_id=turn.user.id,
            anon_map=turn.anon_map,
            abandon_after_seconds=turn.settings.approval_timeout_seconds,
            assembly_context={
                "sanitized_query": turn.sanitized_query,
                "regulation_names": turn.prompt_regulation_names,
                "language": turn.language,
                "output_mode": turn.output_mode,
                "column_descriptions": turn.column_descriptions,
                "query_has_placeholder": turn.query_has_placeholder,
            },
        )
    except ApprovalSessionForbiddenError:
        turn.finished = True
        yield _encode_sse({"type": "error", "message": "Approval session not found."})
        return

    try:
        yield _encode_sse(_approval_required_event(turn, assembled_prompt))

        async with _phase_timer(turn.session_id, "approval_wait"):
            decision_task = asyncio.create_task(
                gate.wait_for_approval(
                    turn.session_id,
                    timeout=turn.settings.approval_timeout_seconds,
                )
            )
            # Emit an SSE comment line every 15s while waiting so the TCP
            # socket stays active. Without this, a slow user decision lets
            # Next.js' proxy (or any other intermediate hop) drop the idle
            # connection, and the answer_chunk events that follow never
            # reach the browser — the UI stays stuck on "Thinking…".
            try:
                while True:
                    done, _ = await asyncio.wait({decision_task}, timeout=15.0)
                    if decision_task in done:
                        decision = decision_task.result()
                        break
                    yield b": keepalive\n\n"
            except BaseException:
                if not decision_task.done():
                    decision_task.cancel()
                raise
    finally:
        # A stream that dies before or during the wait must not leave the
        # session (and its map copy) behind until the sweep.
        gate.discard_session(turn.session_id, approval_session)

    if not decision.approved:
        turn.finished = True
        yield _encode_sse(
            {
                "type": "approval_rejected",
                "session_id": turn.session_id,
                "reason": decision.reason,
                "timed_out": decision.timed_out,
            }
        )
        yield _encode_sse({"type": "end"})
        return

    turn.context_texts = await _gate_edited_chunks(
        offered_texts=turn.context_texts,
        returned_texts=[c.text for c in decision.chunks],
        anon_map=turn.anon_map,
        db=db,
        settings=turn.settings,
    )


def _llm_turn_input(turn: _ChatTurn) -> _LlmTurnInput:
    """Return the parameter object of the turn's LLM call."""
    return _LlmTurnInput(
        sanitized_query=turn.sanitized_query,
        context_chunks=turn.context_texts,
        regulation_names=turn.prompt_regulation_names,
        language=turn.language,
        output_mode=turn.output_mode,
        session_id=turn.session_id,
        column_descriptions=turn.column_descriptions,
        query_has_placeholder=turn.query_has_placeholder,
        query_placeholders=turn.query_placeholders if turn.query_has_placeholder else None,
        rag_mode=turn.rag_mode,
        debug_id=turn.debug_id,
        debug_owner_user_id=turn.user.id,
    )


async def _audit_deanonymization(
    turn: _ChatTurn, settings: AppSettings, db: AsyncSession
) -> None:
    """Record the local de-anonymization in the audit trail (counts only)."""
    if not (settings.deanon_enabled and turn.anon_map.entity_map):
        return
    await log_deanonymization(
        db,
        document_id=turn.document.id if turn.document else 0,
        entity_count=len(turn.anon_map.entity_map),
        strategy=getattr(settings, "deanon_strategy", "simple"),
        session_id=turn.session_id,
    )


async def _record_backend_error(
    db: AsyncSession, http_request: Request, exc: Exception, status_code: int
) -> None:
    """Write ``exc`` to the error log; a failing write is reported, never raised."""
    try:
        await log_backend_error(db, http_request, exc, status_code=status_code)
    except Exception as log_exc:
        report_error_log_failure(log_exc)


async def _llm_error_event(
    exc: LLMRouterError, session_id: str, db: AsyncSession, http_request: Request
) -> dict[str, Any]:
    """Return the error event of a failed LLM call.

    Provider and gateway error text comes from outside the air-gapped zone;
    only a coded error carries a Septum-authored message, everything else
    gets the fixed one.
    """
    logger.warning(
        "chat LLM call failed session_id=%s code=%s error=%s status=400",
        session_id,
        exc.code or LLM_UNAVAILABLE,
        type(exc).__name__,
    )
    await _record_backend_error(db, http_request, exc, 400)
    return {
        "type": "error",
        "code": exc.code or LLM_UNAVAILABLE,
        "message": str(exc) if exc.code else LLM_UNAVAILABLE_MESSAGE,
    }


async def _unexpected_error_event(
    exc: Exception, db: AsyncSession, http_request: Request
) -> dict[str, Any]:
    """Return the error event of an unexpected failure (a refused model gets its code)."""
    refused = isinstance(exc, ModelRefusal)
    _log_stream_failure(exc)
    await _record_backend_error(db, http_request, exc, 503 if refused else 500)
    event: dict[str, Any] = {
        "type": "error",
        "message": MODEL_REFUSED_DETAIL if refused else _UNEXPECTED_ERROR_MESSAGE,
    }
    if refused:
        event["code"] = exc.code
    return event


async def _turn_error_event(
    exc: Exception, session_id: str, db: AsyncSession, http_request: Request
) -> dict[str, Any]:
    """Log a failed turn by type and return the error event the client gets."""
    if isinstance(exc, (DocumentMapMissingError, DocumentGoneError)):
        logger.warning(
            "chat turn refused session_id=%s reason=%s error=%s",
            session_id,
            DOCUMENT_CHANGED,
            type(exc).__name__,
        )
        return {
            "type": "error",
            "code": DOCUMENT_CHANGED,
            "message": (
                "A document changed while the question was being "
                "answered; please ask again."
            ),
        }
    if isinstance(exc, DocumentMapConflictError):
        logger.warning(
            "chat turn refused session_id=%s reason=%s", session_id, MAP_CONFLICT
        )
        return {
            "type": "error",
            "code": MAP_CONFLICT,
            "message": "The document is being updated; please try again.",
        }
    if isinstance(exc, LLMRouterError):
        return await _llm_error_event(exc, session_id, db, http_request)
    return await _unexpected_error_event(exc, db, http_request)


async def _chat_event_stream(
    request: ChatRequest,
    message_text: str,
    document_ids_list: List[int],
    requested_documents: dict[int, Document],
    retry_documents: dict[int, Document],
    db: AsyncSession,
    current_user: User,
    http_request: Request,
) -> AsyncGenerator[bytes, None]:
    """Stream one chat turn as SSE events.

    Phases: sanitize the query, select the context, ``meta``, mask the
    context into the turn's unified numbering, the optional approval gate,
    the LLM call with local de-anonymization, then the answer chunks.
    """
    # Generated up-front so per-phase timing logs can correlate every
    # phase of this turn under a single session_id, even if the user has
    # not supplied one.
    session_id = request.session_id or uuid4().hex
    debug_id = new_debug_id()
    try:
        turn = await _open_turn(
            request,
            message_text,
            document_ids_list,
            requested_documents,
            current_user,
            session_id,
            debug_id,
            db,
        )
        await _sanitize_turn_query(turn, db)
        await _select_turn_context(turn, db, retry_documents)
        await _audit_query_pii(turn, db)
        if turn.rag_mode == "none":
            turn.language = "auto"

        gate: ApprovalGate = get_approval_gate()
        require_approval = _effective_require_approval(
            settings_flag=turn.settings.require_approval,
            request_flag=request.require_approval,
            rag_mode=turn.rag_mode,
        )
        effective_settings = _effective_settings(turn.settings, request)
        yield _encode_sse(_meta_event(turn, require_approval))

        error_event = await _mask_turn_context(turn, db)
        if error_event is not None:
            yield _encode_sse(error_event)
            return
        await _prepare_prompt_inputs(turn, db)
        if require_approval:
            async with contextlib.aclosing(_approval_events(turn, gate, db)) as events:
                async for event in events:
                    yield event
            if turn.finished:
                return

        used_ollama_fallback: List[bool] = [False]
        async with _phase_timer(session_id, "llm_and_deanonymize"):
            answer = await _run_llm_and_deanonymize(
                db,
                effective_settings,
                turn.anon_map,
                _llm_turn_input(turn),
                http_request=http_request,
                used_ollama_fallback_ref=used_ollama_fallback,
            )
        await _audit_deanonymization(turn, effective_settings, db)

        for piece in _chunk_text(answer, max_chunk_size=256):
            yield _encode_sse({"type": "answer_chunk", "text": piece})

        yield _encode_sse({
            "type": "end",
            "used_ollama_fallback": used_ollama_fallback[0],
        })
    except asyncio.CancelledError:
        logger.debug("Client disconnected from chat SSE stream")
        raise
    except Exception as exc:
        yield _encode_sse(await _turn_error_event(exc, session_id, db, http_request))


def _requested_document_ids(request: ChatRequest) -> List[int]:
    """Return the document ids the request names, new or legacy field."""
    if request.document_ids:
        return list(request.document_ids)
    if request.document_id is not None:
        return [request.document_id]
    return []


async def _owned_documents(
    db: AsyncSession, document_ids: List[int], user: User
) -> dict[int, Document]:
    """Resolve the requested documents; a missing and a foreign one give the same 404."""
    documents: dict[int, Document] = {}
    for did in document_ids:
        documents[did] = await get_owned_or_404(db, Document, did, user, _DOCUMENT_NOT_FOUND)
    return documents


async def _owned_retry_documents(
    db: AsyncSession,
    request: ChatRequest,
    requested_documents: dict[int, Document],
    user: User,
) -> dict[int, Document]:
    """Resolve the documents a retry's chunks name.

    The ids pass the same owner check as requested ids, so they can never
    widen access.
    """
    retry_documents: dict[int, Document] = {}
    retry_ids = {
        c.document_id
        for c in request.pre_approved_chunks or []
        if c.document_id is not None
    }
    for did in sorted(retry_ids):
        retry_documents[did] = requested_documents.get(did) or await get_owned_or_404(
            db, Document, did, user, _DOCUMENT_NOT_FOUND
        )
    return retry_documents


@router.post(
    "/ask",
    response_class=StreamingResponse,
    status_code=status.HTTP_200_OK,
)
@route_limit("30/minute", fail_open=True)
async def chat_ask(
    request: ChatRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Chat endpoint that streams an SSE response for a single turn."""
    message_text = request.message or request.query
    if not message_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Either 'message' or 'query' must be provided.",
        )

    document_ids_list = _requested_document_ids(request)
    # Resolved before the stream opens so a missing and a foreign document
    # yield the same HTTP 404 the documents router returns.
    requested_documents = await _owned_documents(db, document_ids_list, current_user)
    retry_documents = await _owned_retry_documents(
        db, request, requested_documents, current_user
    )
    return StreamingResponse(
        _chat_event_stream(
            request,
            message_text,
            document_ids_list,
            requested_documents,
            retry_documents,
            db,
            current_user,
            http_request,
        ),
        media_type="text/event-stream",
    )


@router.get(
    "/debug/{debug_id}",
    status_code=status.HTTP_200_OK,
)
async def chat_debug(
    debug_id: str,
    user: User = Depends(get_current_user),
) -> ChatDebugResponse:
    """Return the masked prompt and masked answer of one chat turn.

    ``debug_id`` is the server-generated id from that turn's SSE ``meta``
    event. Only masked content is returned — never the de-anonymized
    answer, the anonymization map or raw text. Users read the records of
    their own turns; admins read any record. A record owned by another
    user is reported as not found, exactly like a missing one.
    """
    record: ChatDebugRecord | None = get_chat_debug_record(
        debug_id,
        requester_user_id=user.id,
        requester_is_admin=user.role == "admin",
    )
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No debug information found for this debug_id.",
        )
    return ChatDebugResponse(
        debug_id=record.debug_id,
        masked_prompt=record.masked_prompt,
        masked_answer=record.masked_answer,
    )

