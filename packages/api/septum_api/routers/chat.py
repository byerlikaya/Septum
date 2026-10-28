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
from itertools import zip_longest
from typing import TYPE_CHECKING, Any, AsyncGenerator, List, Optional, Sequence, Tuple
from uuid import uuid4

if TYPE_CHECKING:
    from ..services.bm25_retriever import BM25Retriever
    from ..services.vector_store import VectorStore

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def _phase_timer(session_id: str, phase: str) -> AsyncGenerator[None, None]:
    """Log how long a chat pipeline phase takes.

    Wraps a section of the chat ``event_stream`` so that each major phase
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
from ..services.anonymization_map import AnonymizationMap
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
    by_document: dict[int, list[Chunk]] = {}
    for c in chunks:
        by_document.setdefault(c.document_id, []).append(c)
    docs: list[tuple[Document, list[Chunk]]] = []
    for did in sorted(by_document):
        doc = documents_by_id.get(did)
        if doc is None:
            raise LookupError(f"Retrieved chunk of document {did} is outside the turn")
        docs.append((doc, by_document[did]))

    def _outdated(snapshot: PolicySnapshot) -> list[tuple[Document, list[Chunk]]]:
        return [
            (doc, doc_chunks)
            for doc, doc_chunks in docs
            if doc.ingestion_status != "completed"
            or document_staleness(doc, snapshot).needs_reprocessing
        ]

    # Cheap read first, so a turn over fresh documents builds no recognizer.
    if not _outdated(await current_policy_snapshot(db)):
        return {}, set()
    # The sanitizer, the staleness verdict and the cache key all come from
    # this one composition, so text masked under one policy is never cached
    # under another's key.
    policy, current = await PolicyComposer().compose_with_snapshot(db, settings)
    restored: dict[int, str] = {}
    changed: set[int] = set()
    sanitizer = None
    for doc, doc_chunks in _outdated(current):
        doc_map = await get_document_map(doc.id)
        if doc_map is None:
            raise DocumentMapMissingError(f"Document {doc.id} has no anonymization map")
        misses: list[tuple[Chunk, tuple]] = []
        language = effective_masking_language(doc)
        for c in doc_chunks:
            key = remask_cache.key(
                doc.id, c.id, current.cache_key, c.sanitized_text or "", language
            )
            hit = remask_cache.get(key, doc_map)
            if hit is None:
                misses.append((c, key))
            else:
                restored[c.id] = hit
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
    """Re-namespace per-document placeholders into a globally unique set.

    Each document's anon_map is built with a doc-local counter, so two
    documents can both contain ``[ORGANIZATION_2]`` pointing to different
    organizations. Concatenating chunks from both into the LLM prompt
    collapses the two entities into a single placeholder. The cloud LLM
    cannot tell them apart, and the deanonymizer can only resolve the
    collision in one direction — the other side either silently rewrites
    to the wrong original value or leaks the literal placeholder back to
    the user.

    This helper assigns a fresh global counter per entity type and emits:

    * ``per_doc_remap`` — for each document, the
      ``{old_placeholder: new_placeholder}`` rename to apply to that
      document's masked chunk text before assembly. Identical originals
      that recur across documents collapse onto the same new placeholder.
    * ``unified_map`` — a merged :class:`AnonymizationMap` whose
      ``entity_map`` uses the new placeholders. The deanonymizer iterates
      this map after the LLM responds, so every globally unique
      placeholder resolves back to the correct original value.
    """
    global_counter: dict[str, int] = {}
    assigned: dict[tuple[str, str], str] = {}
    per_doc_remap: dict[int, dict[str, str]] = {}

    # Sort by doc id so the global numbering is deterministic across runs.
    for doc_id in sorted(anon_maps.keys()):
        m = anon_maps[doc_id]
        doc_remap: dict[str, str] = {}
        for original, old_placeholder in m.entity_map.items():
            match = _ENTITY_PLACEHOLDER_RE.fullmatch(old_placeholder)
            if not match:
                continue
            entity_type = match.group(1)
            key = (original, entity_type)
            if key in assigned:
                new_placeholder = assigned[key]
            else:
                global_counter[entity_type] = (
                    global_counter.get(entity_type, 0) + 1
                )
                new_placeholder = (
                    f"[{entity_type}_{global_counter[entity_type]}]"
                )
                assigned[key] = new_placeholder
            if new_placeholder != old_placeholder:
                doc_remap[old_placeholder] = new_placeholder
        per_doc_remap[doc_id] = doc_remap

    first_lang = (
        next(iter(anon_maps.values())).language if anon_maps else "en"
    )
    unified_map = AnonymizationMap(document_id=0, language=first_lang)
    for doc_id, m in anon_maps.items():
        doc_remap = per_doc_remap[doc_id]
        for original, old_placeholder in m.entity_map.items():
            new_placeholder = doc_remap.get(old_placeholder, old_placeholder)
            # entity_map is original-keyed and may legitimately collide
            # when the same original was detected as different entity
            # types across documents — the dict will silently drop one
            # of the placeholders. Mirror every (placeholder, original)
            # pair into ``placeholder_lookup`` so the deanonymizer can
            # still resolve every globally-minted placeholder.
            unified_map.entity_map[original] = new_placeholder
            if new_placeholder:
                unified_map.placeholder_lookup[new_placeholder] = original
        for token, old_placeholder in m.token_to_placeholder.items():
            new_placeholder = doc_remap.get(old_placeholder, old_placeholder)
            unified_map.token_to_placeholder[token] = new_placeholder
        unified_map.blocklist.update(m.blocklist)

    return per_doc_remap, unified_map


def _apply_chunk_placeholder_remap(
    text: str, remap: dict[str, str]
) -> str:
    """Rewrite per-document placeholders in ``text`` using ``remap``.

    Single-pass regex substitution rather than sequential ``str.replace``
    calls. The naive sequential approach cascades catastrophically when
    the rename's old- and new-placeholder ranges overlap — e.g. a remap
    of ``{[PERSON_NAME_1]: [PERSON_NAME_2], [PERSON_NAME_2]: [PERSON_NAME_1]}``
    that intends to swap the two placeholders ends up collapsing every
    occurrence onto ``[PERSON_NAME_1]`` because the first pass writes
    ``[PERSON_NAME_2]`` everywhere and the second pass then maps every
    such mention (including the freshly-written ones) back. The single
    regex pass touches each match exactly once, so swap-shaped renames
    produced by the placeholder unification stay correct.

    Alternatives are emitted longest-first so a key like
    ``[ORGANIZATION_10]`` wins over ``[ORGANIZATION_1]`` at the same
    starting offset.
    """
    if not text or not remap:
        return text
    keys_sorted = sorted(remap.keys(), key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(k) for k in keys_sorted))
    return pattern.sub(lambda m: remap[m.group(0)], text)



_QUERY_MAP_KEY = -1


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
        {_QUERY_MAP_KEY: query_map, **doc_maps}
    )
    query_remap = per_doc_remap.pop(_QUERY_MAP_KEY, {})
    return doc_maps, per_doc_remap, query_remap, unified_map


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
        base_chunks = list(db_result.scalars().all())
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
    base_chunks = list(db_result.scalars().all())

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


_PLACEHOLDER_PATTERN = re.compile(r"\[[A-Za-z_]+\d+\]")


def _typed_query_placeholders(
    message_text: str, sanitized_query: str, query_remap: dict[str, str]
) -> List[str]:
    """Return the placeholders the user typed, renumbered as in ``sanitized_query``.

    Each typed placeholder is carried through the query remap of the
    unified numbering and kept only if the sanitized query still holds it.
    """
    typed = _PLACEHOLDER_PATTERN.findall(message_text or "")
    renumbered = [query_remap.get(p, p) for p in typed]
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
    return {p for text in sources if text for p in _PLACEHOLDER_PATTERN.findall(text)}


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


async def _run_llm_and_deanonymize(
    db: AsyncSession,
    settings: AppSettings,
    anon_map: AnonymizationMap,
    sanitized_query: str,
    context_chunks: List[str],
    regulation_names: List[str],
    language: str,
    output_mode: str = "chat",
    session_id: Optional[str] = None,
    column_descriptions: Optional[List[str]] = None,
    query_has_placeholder: bool = False,
    query_placeholders: Optional[List[str]] = None,
    http_request: Optional[Request] = None,
    used_ollama_fallback_ref: Optional[List[bool]] = None,
    rag_mode: str = "manual",
    debug_id: Optional[str] = None,
    debug_owner_user_id: Optional[int] = None,
) -> str:
    """Call the LLM and apply local de-anonymization.

    When ``debug_id`` is given, the masked prompt and masked answer of
    this turn are kept in the in-memory debug store under that id.
    """
    llm = LLMRouter(settings)

    async def on_cloud_failure(message: str, extra: dict[str, Any]) -> None:
        if used_ollama_fallback_ref is not None:
            used_ollama_fallback_ref[0] = True
        if http_request is not None:
            try:
                await log_backend_message(db, http_request, message, level="ERROR", extra=extra)
            except Exception as log_exc:
                report_error_log_failure(log_exc)

    if rag_mode == "none":
        user_prompt = PromptCatalog.pure_chat_prompt(sanitized_query)
    else:
        user_prompt = _assemble_user_prompt(
            sanitized_query=sanitized_query,
            context_chunks=context_chunks,
            regulation_names=regulation_names,
            language=language,
            output_mode=output_mode,
            column_descriptions=column_descriptions,
            query_has_placeholder=query_has_placeholder,
        )

    # Keep the placeholder-aware list available for downstream answer
    # post-processing (which needs to know which placeholders were actually
    # present in the context).
    context_text_for_placeholders = ""
    if context_chunks:
        lines_tmp: List[str] = []
        for idx, chunk in enumerate(context_chunks, start=1):
            lines_tmp.append(f"Chunk {idx}:\n{chunk}")
        context_text_for_placeholders = "\n\n".join(lines_tmp)
    placeholders_in_context = _extract_placeholders_from_context(
        context_text_for_placeholders
    )

    messages = [
        {"role": "user", "content": user_prompt},
    ]

    masked_answer = await llm.complete(
        messages=messages,
        on_cloud_failure=on_cloud_failure if http_request is not None else None,
    )

    if query_placeholders:
        # For explicit placeholder questions, always answer strictly with the
        # queried placeholder tokens only, to avoid leaking additional linked
        # attributes (e.g. identifiers, locations) beyond what was asked.
        unique_query_placeholders: List[str] = []
        seen_ph: set[str] = set()
        for token in query_placeholders:
            if token not in seen_ph:
                seen_ph.add(token)
                unique_query_placeholders.append(token)
        masked_answer = " ".join(unique_query_placeholders)
    elif placeholders_in_context and query_has_placeholder and not _PLACEHOLDER_PATTERN.search(
        masked_answer
    ):
        is_refusal = any(
            p in masked_answer.lower() for p in PromptCatalog.REFUSAL_PHRASES
        )
        bullet_lines = [
            line.strip()
            for line in masked_answer.splitlines()
            if line.strip() and line.strip().startswith(("•", "-", "*"))
        ]
        if is_refusal or len(bullet_lines) >= 5:
            masked_answer = "\n".join(f"- {p}" for p in placeholders_in_context)

    # Only placeholders this turn's data exposed to the LLM are restored;
    # any other placeholder in the answer stays literal.
    if rag_mode == "none":
        prompt_placeholders = _sent_placeholders(sanitized_query, [], [], [])
    else:
        prompt_placeholders = _sent_placeholders(
            sanitized_query,
            context_chunks,
            list(column_descriptions or []),
            regulation_names,
        )
    deanonymizer = Deanonymizer(settings=settings)
    deanonymized = deanonymizer.deanonymize(
        masked_answer, anon_map, allowed_placeholders=prompt_placeholders
    )

    # Diagnostic: report any placeholders that survived deanonymization so
    # we can tell at a glance whether the LLM emitted a placeholder the
    # unified anon_map did not carry an entry for (the failure mode that
    # leaks ``[PERSON_NAME_1]`` literals to the user).
    answer_placeholders = sorted(set(_PLACEHOLDER_PATTERN.findall(masked_answer)))
    residual_placeholders = sorted(set(_PLACEHOLDER_PATTERN.findall(deanonymized)))
    map_placeholders = (
        set(anon_map.placeholder_lookup.keys())
        if anon_map.placeholder_lookup
        else (set(anon_map.entity_map.values()) if anon_map.entity_map else set())
    )
    if answer_placeholders or residual_placeholders:
        logger.info(
            "chat deanon session_id=%s strategy=%s map_size=%s "
            "answer_placeholders=%s residual=%s missing_in_map=%s",
            session_id,
            getattr(settings, "deanon_strategy", "simple"),
            len(map_placeholders),
            answer_placeholders,
            residual_placeholders,
            [p for p in answer_placeholders if p not in map_placeholders],
        )

    normalizer = TextNormalizer()
    result = await normalizer.normalize(db, deanonymized)
    if debug_id is not None:
        set_chat_debug_record(
            debug_id=debug_id,
            owner_user_id=debug_owner_user_id,
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
    response_model=AnalyzeQueryResponse,
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
            db, Document, body.document_ids[0], user, "Document not found."
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
    narrowed = [d for ids in cluster_lists for d in ids] if requires else (
        cluster_lists[0] if cluster_lists else []
    )

    return AnalyzeQueryResponse(
        requires_disambiguation=requires,
        clusters=cluster_payload,
        narrowed_doc_ids=narrowed,
        reason="multi_cluster_ambiguity" if requires else "single_cluster",
    )


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

    document_ids_list: List[int] = []
    if request.document_ids:
        document_ids_list = list(request.document_ids)
    elif request.document_id is not None:
        document_ids_list = [request.document_id]
    document_id = document_ids_list[0] if document_ids_list else None

    # Resolved before the stream opens so a missing and a foreign document
    # yield the same HTTP 404 the documents router returns.
    requested_documents: dict[int, Document] = {}
    for did in document_ids_list:
        requested_documents[did] = await get_owned_or_404(
            db, Document, did, current_user, "Document not found."
        )
    # A retry's chunks name the documents they were offered from; the ids
    # pass the same owner check so they can never widen access.
    retry_documents: dict[int, Document] = {}
    retry_ids = {
        c.document_id
        for c in request.pre_approved_chunks or []
        if c.document_id is not None
    }
    for did in sorted(retry_ids):
        retry_documents[did] = requested_documents.get(did) or await get_owned_or_404(
            db, Document, did, current_user, "Document not found."
        )

    async def event_stream() -> AsyncGenerator[bytes, None]:
        # Generated up-front so per-phase timing logs can correlate every
        # phase of this turn under a single session_id, even if the user has
        # not supplied one.
        session_id = request.session_id or uuid4().hex
        debug_id = new_debug_id()
        try:
            settings = await load_settings(db)
            active_regulations = await _active_regulations(db)
            regulation_names = [name for name, _ in active_regulations]

            documents_by_id: dict[int, Document] = dict(requested_documents)
            document: Document | None = (
                documents_by_id.get(document_id)
                if document_id is not None
                else None
            )

            language = query_language(message_text, document)

            # The document whose map seeds the query map; part of the
            # numbering fingerprint a retry is checked against.
            query_seed_document_id = document.id if document is not None else None
            if document is not None:
                # The query is sanitized against a copy: the cached map object
                # is shared by every request and must never gain query PII.
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
                    getattr(settings, "deanon_enabled", True),
                )
                if map_entries == 0 and getattr(
                    settings, "deanon_enabled", True
                ):
                    logger.warning(
                        "Deanonymization will have no effect: no map for document_id=%s. "
                        "Encryption key may have changed — re-upload the document so the map can be loaded.",
                        document.id,
                    )
            else:
                anon_map = AnonymizationMap(
                    document_id=0, language=language
                )
                logger.info(
                    "chat document_id=None anon_map_entries=%s deanon_enabled=%s",
                    len(anon_map.entity_map),
                    getattr(settings, "deanon_enabled", True),
                )

            async with _phase_timer(session_id, "sanitize_query"):
                sanitized_query, entity_count, query_type_counts = (
                    await _sanitize_query(
                        message_text, language, settings, anon_map, db
                    )
                )
            relevance_threshold = settings.rag_relevance_threshold
            rag_mode: str = "manual" if document_ids_list else "auto"
            matched_document_ids: List[int] = []
            matched_document_names: List[str] = []
            chunks: List[Chunk] = []

            if request.pre_approved_chunks is None and rag_mode == "manual" and documents_by_id:
                async with _phase_timer(session_id, "retrieve_chunks"):
                    base_top_k = request.top_k or settings.top_k_retrieval
                    total_chunk_count = sum(
                        d.chunk_count for d in documents_by_id.values()
                    )
                    top_k = _effective_top_k(base_top_k, total_chunk_count)
                    if len(documents_by_id) == 1:
                        doc = next(iter(documents_by_id.values()))
                        chunks = await _retrieve_chunks(
                            db=db,
                            document_id=doc.id,
                            query=sanitized_query,
                            top_k=base_top_k,
                            chunk_count=doc.chunk_count,
                            relevance_threshold=relevance_threshold,
                        )
                    else:
                        MIN_PER_DOC = 10
                        num_docs = len(documents_by_id)
                        per_doc_k = max(
                            MIN_PER_DOC,
                            min(MAX_CONTEXT_CHUNKS // num_docs, top_k // num_docs),
                        )
                        for doc in documents_by_id.values():
                            chunks_doc = await _retrieve_chunks(
                                db=db,
                                document_id=doc.id,
                                query=sanitized_query,
                                top_k=per_doc_k,
                                chunk_count=doc.chunk_count,
                                relevance_threshold=relevance_threshold,
                            )
                            chunks.extend(chunks_doc)
                    # Every offer must fit the retry limit, so a rejected
                    # turn can always be sent again.
                    chunks = _cap_context_chunks(chunks, MAX_CONTEXT_CHUNKS)

            elif request.pre_approved_chunks is not None and retry_documents:
                # Same document set (and lead document) the original turn
                # unified its placeholders over, so the retry rebuilds the
                # identical numbering; in auto mode no ids were requested.
                for did, doc in retry_documents.items():
                    documents_by_id.setdefault(did, doc)
                if document is None:
                    document = next(iter(documents_by_id.values()))

            elif request.pre_approved_chunks is None and rag_mode == "auto":
                async with _phase_timer(session_id, "auto_rag_classify"):
                    all_user_docs_result = await db.execute(
                        select(Document).where(
                            _auto_rag_owner_filter(current_user),
                            Document.ingestion_status == "completed",
                            Document.chunk_count > 0,
                        )
                    )
                    all_user_docs = list(
                        all_user_docs_result.scalars().all()
                    )

                    if not all_user_docs:
                        rag_mode = "none"
                    elif request.scoped_doc_ids:
                        # Disambiguation picker already chose a specific
                        # document set (sent back via ``analyze_query``);
                        # honour it verbatim and skip the entity-aware
                        # narrowing pass.
                        scope = set(request.scoped_doc_ids)
                        before = len(all_user_docs)
                        all_user_docs = [d for d in all_user_docs if d.id in scope]
                        logger.info(
                            "chat entity-routing session_id=%s reason=disambiguation_choice "
                            "candidates=%d narrowed=%d",
                            session_id,
                            before,
                            len(all_user_docs),
                        )
                    else:
                        # Entity-aware narrowing: if the user's question
                        # mentioned a specific PII value (a person, an
                        # IBAN, a national ID …) we can confidently scope
                        # retrieval to only the documents that actually
                        # contain that value, instead of mixing chunks
                        # across unrelated docs and risking the cloud
                        # LLM attributing one person's data to another.
                        # When no query entity matches the index we fall
                        # back to the original "every document is a
                        # candidate" behaviour. The match is HMAC-keyed
                        # under the local encryption key so we never
                        # leak originals into the index lookup.
                        query_entities = [
                            (original, _extract_entity_type(placeholder))
                            for original, placeholder in anon_map.entity_map.items()
                            if placeholder
                        ]
                        query_entities = [
                            (val, etype) for val, etype in query_entities if etype
                        ]
                        narrowed_doc_ids: set[int] = set()
                        narrowing_reason = "no_entity_in_query"
                        if query_entities:
                            entity_scores = await find_documents_for_query_entities(
                                db,
                                query_entities,
                                owner_id=None if current_user.role == "admin" else current_user.id,
                            )
                            if entity_scores:
                                strong = {
                                    did
                                    for did, sc in entity_scores.items()
                                    if sc >= RELATIONSHIP_THRESHOLD_STRONG
                                }
                                medium = {
                                    did
                                    for did, sc in entity_scores.items()
                                    if sc >= RELATIONSHIP_THRESHOLD_MEDIUM
                                }
                                if strong:
                                    narrowed_doc_ids = strong
                                    narrowing_reason = "strong_entity_match"
                                elif medium:
                                    narrowed_doc_ids = medium
                                    narrowing_reason = "medium_entity_match"
                                else:
                                    # Only weak (e.g. shared LOCATION).
                                    # Don't narrow — a city name match
                                    # is too noisy to scope on alone.
                                    narrowing_reason = "weak_entity_match_only"

                        if narrowed_doc_ids:
                            before = len(all_user_docs)
                            all_user_docs = [
                                d for d in all_user_docs if d.id in narrowed_doc_ids
                            ]
                            logger.info(
                                "chat entity-routing session_id=%s reason=%s "
                                "candidates=%d narrowed=%d",
                                session_id,
                                narrowing_reason,
                                before,
                                len(all_user_docs),
                            )

                        # When entity routing already proved that the
                        # query references PII that lives in the corpus
                        # we have hard evidence the turn is about the
                        # documents — skip the Ollama-based intent
                        # classifier entirely. The classifier is
                        # non-deterministic (the same Turkish question
                        # asked twice in a row produced "auto" then
                        # "chat" verdicts in production logs) and
                        # routinely tagged document-grounded questions
                        # as general chat, sending the LLM to answer
                        # from its own knowledge. ANY entity match -
                        # strong, medium, or even weak - means the user
                        # mentioned something that exists in their
                        # corpus, which is the most reliable signal we
                        # have that the question is about the documents.
                        # The classifier is only consulted in the
                        # fallback path where the query carries no
                        # entity at all (or the entity isn't in the
                        # index).
                        if narrowing_reason in (
                            "strong_entity_match",
                            "medium_entity_match",
                            "weak_entity_match_only",
                        ):
                            logger.info(
                                "chat intent-classifier session_id=%s skipped reason=%s",
                                session_id,
                                narrowing_reason,
                            )
                        else:
                            doc_names = [
                                d.original_filename or f"document_{d.id}"
                                for d in all_user_docs
                            ]
                            intent = await _classify_query_intent(
                                query=sanitized_query,
                                document_names=doc_names,
                                ollama_base_url=settings.ollama_base_url,
                                ollama_model=settings.ollama_chat_model,
                            )
                            if intent == "chat":
                                rag_mode = "none"

                if rag_mode == "auto":
                    async with _phase_timer(session_id, "retrieve_chunks_auto"):
                        base_top_k = request.top_k or settings.top_k_retrieval
                        chunks, matched_document_ids = await _retrieve_chunks_all_documents(
                            db=db,
                            query=sanitized_query,
                            top_k=base_top_k,
                            documents=all_user_docs,
                            relevance_threshold=relevance_threshold,
                        )
                        if chunks:
                            docs_by_id = {
                                d.id: d for d in all_user_docs
                            }
                            for did in matched_document_ids:
                                doc_obj = docs_by_id.get(did)
                                if doc_obj:
                                    documents_by_id[did] = doc_obj
                                    matched_document_names.append(
                                        doc_obj.original_filename
                                        or str(did)
                                    )
                            document = next(
                                iter(documents_by_id.values()), None
                            )
                        else:
                            rag_mode = "none"

            if entity_count > 0:
                await log_pii_detected(
                    db,
                    document_id=document.id if document else 0,
                    regulation_ids=(
                        list(document.active_regulation_ids)
                        if document
                        else []
                    ),
                    entity_type_counts=query_type_counts,
                    total_count=entity_count,
                    session_id=session_id,
                    extra={"source": "chat_query"},
                )

            if rag_mode == "none":
                language = "auto"

            gate: ApprovalGate = get_approval_gate()
            require_approval = _effective_require_approval(
                settings_flag=settings.require_approval,
                request_flag=request.require_approval,
                rag_mode=rag_mode,
            )

            effective_settings = settings
            if request.deanon_enabled is not None:
                effective_settings = copy.copy(settings)
                effective_settings.deanon_enabled = request.deanon_enabled

            chunks_per_doc: dict[int, int] = {}
            for c in chunks:
                chunks_per_doc[c.document_id] = (
                    chunks_per_doc.get(c.document_id, 0) + 1
                )
            matched_documents = [
                {
                    "id": did,
                    "name": (
                        documents_by_id[did].original_filename
                        if did in documents_by_id
                        and documents_by_id[did].original_filename
                        else str(did)
                    ),
                    "chunk_count": count,
                }
                for did, count in sorted(
                    chunks_per_doc.items(), key=lambda kv: -kv[1]
                )
            ]

            yield _encode_sse(
                {
                    "type": "meta",
                    "session_id": session_id,
                    "debug_id": debug_id,
                    "document_id": (
                        document.id if document is not None else None
                    ),
                    "language": language,
                    "require_approval": require_approval,
                    "retrieved_chunk_count": len(chunks),
                    "active_regulations": regulation_names,
                    "rag_mode": rag_mode,
                    "matched_document_ids": matched_document_ids,
                    "matched_document_names": matched_document_names,
                    "matched_documents": matched_documents,
                }
            )

            # Compute output_mode and placeholder info up-front so the
            # approval-modal assembled-prompt preview uses exactly the same
            # parameters the real LLM call will use, and so both the
            # pre-approved and fresh-approval branches share one definition.
            output_mode = (request.output_mode or "chat").strip().lower()

            # Fingerprint of everything the unified placeholder numbering is
            # built from; the approval event carries it signed so a later
            # retry can prove it rebuilt the same numbering.
            query_remap: dict[str, str] = {}
            numbering_fp: bytes
            candidate_chunks: List[ApprovalChunk] = []
            if request.pre_approved_chunks is not None:
                client_texts = [c.text for c in request.pre_approved_chunks]
                query_map = anon_map
                doc_maps: dict[int, AnonymizationMap] = {}
                if documents_by_id:
                    # The client returns chunks numbered in the unified space
                    # of the turn that produced them; rebuild that same space
                    # so every placeholder resolves to the entity it meant.
                    doc_maps, _, query_remap, anon_map = await _unify_turn_placeholders(
                        query_map, list(documents_by_id.keys()), language
                    )
                numbering_fp = numbering_fingerprint(
                    query_map, doc_maps, query_seed_document_id
                )
                # A rebuilt numbering is only trusted when it matches the one
                # the chunks were produced under (maps, selection and query
                # unchanged since); otherwise nothing is sent.
                if (doc_maps or request.numbering_token is not None) and not verify_numbering(
                    request.numbering_token, numbering_fp, current_user.id
                ):
                    logger.warning(
                        "chat retry refused session_id=%s reason=%s docs=%s",
                        session_id,
                        RETRY_NUMBERING_CHANGED,
                        sorted(doc_maps),
                    )
                    yield _encode_sse(
                        {
                            "type": "error",
                            "code": RETRY_NUMBERING_CHANGED,
                            "message": (
                                "The documents changed since this answer was "
                                "prepared; please ask again."
                            ),
                        }
                    )
                    return
                if query_remap:
                    sanitized_query = _apply_chunk_placeholder_remap(
                        sanitized_query, query_remap
                    )
                # Entities first seen in the client text must not reuse a
                # number the client text already carries for someone else.
                reserve_placeholder_counters(anon_map, client_texts)
                client_texts = await gate_untrusted_texts(
                    client_texts, anon_map, db, settings
                )
                candidate_chunks = [
                    ApprovalChunk(id=None, document_id=c.document_id, text=text)
                    for c, text in zip(request.pre_approved_chunks, client_texts)
                ]
            else:
                sanitized_chunk_texts: List[str] = []
                numbering_fp = numbering_fingerprint(
                    anon_map, {}, query_seed_document_id
                )
                if documents_by_id:
                    # Before the maps are loaded: entities the current policy
                    # finds in outdated chunks join their document's map.
                    async with _phase_timer(session_id, "resanitize_outdated_chunks"):
                        restored_chunk_texts, remasked_doc_ids = (
                            await _resanitize_outdated_chunks(
                                db, settings, chunks, documents_by_id
                            )
                        )
                    if query_seed_document_id in remasked_doc_ids:
                        # The query map was seeded from the seed document's
                        # map before the re-mask grew it; a retry seeds from
                        # the grown map, so seed and sanitize again from it
                        # or the retry's numbering would never match.
                        seed_map = await get_document_map(query_seed_document_id)
                        if seed_map is None:
                            raise DocumentMapMissingError(
                                f"Document {query_seed_document_id} has no anonymization map"
                            )
                        anon_map = copy.deepcopy(seed_map)
                        sanitized_query, entity_count, _ = (
                            await _sanitize_query(
                                message_text, language, settings, anon_map, db
                            )
                        )
                    # Every turn document's map is reloaded and unified with
                    # the query map (see ``_unify_turn_placeholders``): the
                    # outer ``anon_map`` may be the empty map auto-RAG started
                    # with, and query entities must share the document's
                    # placeholder so the LLM sees one entity. A turn without
                    # chunks is unified too, so its retry rebuilds the same
                    # numbering.
                    query_anon_map = anon_map
                    anon_maps, per_doc_remap, query_remap, anon_map = (
                        await _unify_turn_placeholders(
                            query_anon_map, list(documents_by_id.keys()), language
                        )
                    )
                    numbering_fp = numbering_fingerprint(
                        query_anon_map, anon_maps, query_seed_document_id
                    )
                    if query_remap:
                        sanitized_query = _apply_chunk_placeholder_remap(
                            sanitized_query, query_remap
                        )

                    # Log placeholder shape only — never the originals — so
                    # we can diagnose unification gaps without writing PII
                    # to the local log file.
                    logger.info(
                        "chat unified-map session_id=%s docs=%s "
                        "unified_entries=%s query_entries=%s "
                        "query_remap_size=%s placeholders=%s",
                        session_id,
                        sorted(anon_maps.keys()),
                        len(anon_map.entity_map),
                        len(query_anon_map.entity_map),
                        len(query_remap),
                        sorted(set(anon_map.entity_map.values()))[:20],
                    )

                    # Documents are labelled by position, never by filename:
                    # an original filename routinely carries personal data
                    # and must not reach the cloud LLM.
                    document_labels: dict[int, int] = {}
                    prev_doc_id: Optional[int] = None
                    for c in chunks:
                        header = ""
                        if (
                            len(documents_by_id) > 1
                            and c.document_id != prev_doc_id
                        ):
                            label = document_labels.setdefault(
                                c.document_id, len(document_labels) + 1
                            )
                            header = PromptCatalog.document_context_header(label)
                            prev_doc_id = c.document_id
                        chunk_text = restored_chunk_texts.get(
                            c.id, c.sanitized_text or ""
                        )
                        chunk_map = anon_maps.get(c.document_id, anon_map)
                        # Chunks were analysed at ingestion; the map is the source of truth here.
                        masked_chunk = mask_known_entities(chunk_text, chunk_map)
                        chunk_remap = per_doc_remap.get(c.document_id)
                        if chunk_remap:
                            masked_chunk = _apply_chunk_placeholder_remap(
                                masked_chunk, chunk_remap
                            )
                        sanitized_chunk_texts.append(
                            header + masked_chunk
                        )

                if documents_by_id and chunks:
                    candidate_chunks = _build_approval_chunks(
                        chunks, sanitized_chunk_texts
                    )

            # Placeholders the user typed, in the numbering the LLM sees: the
            # forced answer and the de-anonymization allow-set both read
            # ``sanitized_query``, so they must agree on every number.
            query_placeholders = _typed_query_placeholders(
                message_text, sanitized_query, query_remap
            )
            query_has_placeholder = bool(query_placeholders)

            context_texts = [c.text for c in candidate_chunks]
            prompt_regulation_names = (
                await _prompt_regulation_names(
                    active_regulations, anon_map, db, settings
                )
                if rag_mode != "none"
                else regulation_names
            )
            column_descriptions = (
                await _schema_column_descriptions(
                    db,
                    document.id if document is not None else None,
                    anon_map,
                    settings,
                )
                if rag_mode != "none"
                else []
            )
            if require_approval:
                # Build the exact masked prompt that would be sent to the
                # cloud LLM right now so the approval modal can show it
                # byte-for-byte. If the user edits chunks in the modal a
                # dedicated preview endpoint re-runs this with the edited
                # chunks to keep the preview in sync.
                assembled_prompt = _assemble_user_prompt(
                    sanitized_query=sanitized_query,
                    context_chunks=[c.text for c in candidate_chunks],
                    regulation_names=prompt_regulation_names,
                    language=language,
                    output_mode=output_mode,
                    column_descriptions=column_descriptions,
                    query_has_placeholder=query_has_placeholder,
                )

                try:
                    approval_session = await gate.create(
                        session_id=session_id,
                        masked_prompt=sanitized_query,
                        masked_chunks=[
                            c.text for c in candidate_chunks
                        ],
                        entity_count=entity_count,
                        owner_user_id=current_user.id,
                        anon_map=anon_map,
                        abandon_after_seconds=settings.approval_timeout_seconds,
                        assembly_context={
                            "sanitized_query": sanitized_query,
                            "regulation_names": prompt_regulation_names,
                            "language": language,
                            "output_mode": output_mode,
                            "column_descriptions": column_descriptions,
                            "query_has_placeholder": query_has_placeholder,
                        },
                    )
                except ApprovalSessionForbiddenError:
                    yield _encode_sse(
                        {
                            "type": "error",
                            "message": "Approval session not found.",
                        }
                    )
                    return

                try:
                    yield _encode_sse(
                        {
                            "type": "approval_required",
                            "session_id": session_id,
                            "numbering_token": sign_numbering(
                                numbering_fp, current_user.id
                            ),
                            "masked_prompt": sanitized_query,
                            "assembled_prompt": assembled_prompt,
                            "chunks": [
                                {
                                    "id": c.id,
                                    "document_id": c.document_id,
                                    "text": c.text,
                                    "source_page": c.source_page,
                                    "section_title": c.section_title,
                                }
                                for c in candidate_chunks
                            ],
                        }
                    )

                    async with _phase_timer(session_id, "approval_wait"):
                        decision_task = asyncio.create_task(
                            gate.wait_for_approval(
                                session_id,
                                timeout=settings.approval_timeout_seconds,
                            )
                        )
                        # Emit an SSE comment line every 15s while waiting so
                        # the TCP socket stays active. Without this, a slow
                        # user decision lets Next.js' proxy (or any other
                        # intermediate hop) drop the idle connection, and the
                        # answer_chunk events that follow never reach the
                        # browser — the UI stays stuck on "Thinking…".
                        try:
                            while True:
                                done, _ = await asyncio.wait(
                                    {decision_task}, timeout=15.0
                                )
                                if decision_task in done:
                                    decision = decision_task.result()
                                    break
                                yield b": keepalive\n\n"
                        except BaseException:
                            if not decision_task.done():
                                decision_task.cancel()
                            raise
                finally:
                    # A stream that dies before or during the wait must not leave
                    # the session (and its map copy) behind until the sweep.
                    gate.discard_session(session_id, approval_session)

                if not decision.approved:
                    yield _encode_sse(
                        {
                            "type": "approval_rejected",
                            "session_id": session_id,
                            "reason": decision.reason,
                            "timed_out": decision.timed_out,
                        }
                    )
                    yield _encode_sse({"type": "end"})
                    return

                context_texts = await _gate_edited_chunks(
                    offered_texts=context_texts,
                    returned_texts=[c.text for c in decision.chunks],
                    anon_map=anon_map,
                    db=db,
                    settings=settings,
                )
            used_ollama_fallback: List[bool] = [False]
            async with _phase_timer(session_id, "llm_and_deanonymize"):
                answer = await _run_llm_and_deanonymize(
                    db=db,
                    settings=effective_settings,
                    anon_map=anon_map,
                    sanitized_query=sanitized_query,
                    context_chunks=context_texts,
                    regulation_names=prompt_regulation_names,
                    language=language,
                    output_mode=output_mode,
                    session_id=session_id,
                    column_descriptions=column_descriptions,
                    query_has_placeholder=query_has_placeholder,
                    query_placeholders=(
                        query_placeholders if query_has_placeholder else None
                    ),
                    http_request=http_request,
                    used_ollama_fallback_ref=used_ollama_fallback,
                    rag_mode=rag_mode,
                    debug_id=debug_id,
                    debug_owner_user_id=current_user.id,
                )

            if effective_settings.deanon_enabled and anon_map.entity_map:
                await log_deanonymization(
                    db,
                    document_id=document.id if document else 0,
                    entity_count=len(anon_map.entity_map),
                    strategy=getattr(
                        effective_settings, "deanon_strategy", "simple"
                    ),
                    session_id=session_id,
                )

            for piece in _chunk_text(answer, max_chunk_size=256):
                yield _encode_sse(
                    {"type": "answer_chunk", "text": piece}
                )

            yield _encode_sse({
                "type": "end",
                "used_ollama_fallback": used_ollama_fallback[0],
            })
        except asyncio.CancelledError:
            logger.debug("Client disconnected from chat SSE stream")
            raise
        except (DocumentMapMissingError, DocumentGoneError) as exc:
            logger.warning(
                "chat turn refused session_id=%s reason=%s error=%s",
                session_id,
                DOCUMENT_CHANGED,
                type(exc).__name__,
            )
            yield _encode_sse(
                {
                    "type": "error",
                    "code": DOCUMENT_CHANGED,
                    "message": (
                        "A document changed while the question was being "
                        "answered; please ask again."
                    ),
                }
            )
        except DocumentMapConflictError:
            logger.warning(
                "chat turn refused session_id=%s reason=%s", session_id, MAP_CONFLICT
            )
            yield _encode_sse(
                {
                    "type": "error",
                    "code": MAP_CONFLICT,
                    "message": "The document is being updated; please try again.",
                }
            )
        except LLMRouterError as exc:
            # Provider and gateway error text comes from outside the
            # air-gapped zone; only a coded error carries a Septum-authored
            # message, everything else gets the fixed one.
            logger.warning(
                "chat LLM call failed session_id=%s code=%s error=%s status=400",
                session_id,
                exc.code or LLM_UNAVAILABLE,
                type(exc).__name__,
            )
            try:
                await log_backend_error(
                    db, http_request, exc, status_code=400
                )
            except Exception as log_exc:
                report_error_log_failure(log_exc)
            yield _encode_sse(
                {
                    "type": "error",
                    "code": exc.code or LLM_UNAVAILABLE,
                    "message": str(exc) if exc.code else LLM_UNAVAILABLE_MESSAGE,
                }
            )
        except Exception as exc:
            refused = isinstance(exc, ModelRefusal)
            _log_stream_failure(exc)
            try:
                await log_backend_error(
                    db, http_request, exc, status_code=503 if refused else 500
                )
            except Exception as log_exc:
                report_error_log_failure(log_exc)
            event = {
                "type": "error",
                "message": MODEL_REFUSED_DETAIL
                if refused
                else "An unexpected error occurred while processing the chat request.",
            }
            if refused:
                event["code"] = exc.code
            yield _encode_sse(event)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get(
    "/debug/{debug_id}",
    response_model=ChatDebugResponse,
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

