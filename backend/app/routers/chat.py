from __future__ import annotations

"""FastAPI router for chat with full privacy-preserving RAG pipeline.

Pipeline:
    sanitize (query) → retrieve chunks → optional approval gate →
    LLM (cloud) → local de-anonymization → SSE response.
"""

import asyncio
import copy
import json
import logging
import os
import re
from typing import Any, AsyncGenerator, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from langdetect import DetectorFactory, LangDetectException, detect
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.document import Chunk, Document
from ..models.spreadsheet_schema import SpreadsheetSchema, SpreadsheetColumn
from ..models.regulation import RegulationRuleset
from ..models.settings import AppSettings
from ..services.anonymization_map import AnonymizationMap
from ..services.approval_gate import ApprovalChunk, ApprovalGate, get_approval_gate
from ..services.document_anon_store import get_document_map
from ..services.deanonymizer import Deanonymizer
from ..services.llm_router import LLMRouter, LLMRouterError, _chunk_text
from ..services.ner_model_registry import NERModelRegistry
from ..services.policy_composer import PolicyComposer
from ..services.sanitizer import PIISanitizer
from ..services.text_normalizer import TextNormalizer
from ..services.chat_context import ChatContextPayload, build_chat_prompt
from ..services.desktop_assistant.base import (
    DesktopAssistantError,
    DesktopAssistantTarget,
)
from ..services.desktop_assistant.factory import create_desktop_assistant
from ..services.chat_debug_store import set_chat_debug_record, get_chat_debug_record, ChatDebugRecord
from ..services.error_logger import log_backend_error, log_backend_message
from ..services.prompts import PromptCatalog


router = APIRouter(prefix="/api/chat", tags=["chat"])


DetectorFactory.seed = 42


class ChatRequest(BaseModel):
    """Incoming chat request from the frontend.

    This model supports both the newer v5-style payload
    (``message``, ``document_id``) and the legacy STEP 14
    payload shape (``query``, ``document_ids``, etc.).
    """

    message: Optional[str] = None
    document_id: Optional[int] = None
    top_k: Optional[int] = None
    session_id: Optional[str] = None

    query: Optional[str] = None
    document_ids: Optional[List[int]] = None
    output_mode: Optional[str] = "chat"
    require_approval: Optional[bool] = None
    deanon_enabled: Optional[bool] = None


class ChatDebugResponse(BaseModel):
    """Debug payload describing what was sent to and received from the cloud LLM."""

    session_id: str
    masked_prompt: str
    masked_answer: str
    final_answer: str


class DesktopAssistantRequest(BaseModel):
    """Request body for sending a message to a local desktop assistant client."""

    message: str
    target: DesktopAssistantTarget
    open_new_chat: bool = False
    use_rag: bool = False
    document_ids: list[int] = []
    top_k: int = 5
    skip_approval: bool = False


class DesktopAssistantResponse(BaseModel):
    """Response envelope for desktop assistant send operations."""

    status: str
    message: Optional[str] = None
    prompt: Optional[str] = None
    requires_approval: Optional[bool] = None


async def _load_settings(db: AsyncSession) -> AppSettings:
    result = await db.execute(select(AppSettings).where(AppSettings.id == 1))
    settings = result.scalar_one_or_none()
    if settings is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Application settings have not been initialized.",
        )
    env_llm_model = os.getenv("LLM_MODEL")
    if env_llm_model:
        settings.llm_model = env_llm_model
    env_llm_provider = os.getenv("LLM_PROVIDER")
    if env_llm_provider:
        settings.llm_provider = env_llm_provider
    return settings


async def _load_document(db: AsyncSession, document_id: int) -> Document:
    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalar_one_or_none()
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )
    return document


async def _active_regulation_names(db: AsyncSession) -> List[str]:
    result = await db.execute(
        select(RegulationRuleset.display_name).where(RegulationRuleset.is_active.is_(True))
    )
    return [row[0] for row in result.all()]


async def _detect_language(text: str, fallback: str = "en") -> str:
    if not text:
        return fallback
    try:
        return detect(text)
    except LangDetectException:
        return fallback


async def _sanitize_query(
    message: str,
    language: str,
    settings: AppSettings,
    anon_map: AnonymizationMap,
    db: AsyncSession,
) -> tuple[str, int]:
    """Run the sanitizer on the incoming user message."""
    composer = PolicyComposer()
    policy = await composer.compose(db)
    overrides = getattr(settings, "ner_model_overrides", None) or {}
    ner_registry = NERModelRegistry(_overrides=dict(overrides))
    sanitizer = PIISanitizer(
        settings=settings,
        policy=policy,
        ner_registry=ner_registry,
        enable_ollama_layer=True,  # Enable validation layer at query time
    )
    result = await asyncio.to_thread(
        sanitizer.sanitize,
        message,
        language,
        anon_map,
    )
    return result.sanitized_text, result.entity_count


THEME_SNIPPET_LEN = 400
MIN_CHUNKS_FOR_LAST = 6
TOP_K_RETRIEVAL_MAX_CAP = 25
FULL_DOCUMENT_CHUNK_THRESHOLD = 100


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


async def _retrieve_chunks(
    db: AsyncSession,
    document_id: int,
    query: str,
    top_k: int,
    chunk_count: Optional[int] = None,
) -> List[Chunk]:
    """Retrieve top-k chunks using hybrid search (BM25 + FAISS).

    Combines keyword-based BM25 search with semantic FAISS search using
    Reciprocal Rank Fusion (RRF). When chunk_count > 1, also runs a
    document-theme search (first chunk snippet) and merges with RRF so
    holistic queries get both query-relevant and document-representative chunks.
    Falls back to FAISS-only if BM25 index is unavailable, and to first
    top_k chunks if neither index exists.

    The first chunk (index 0) is always included when the document has multiple
    chunks. For long documents (chunk_count >= MIN_CHUNKS_FOR_LAST), the last
    chunk is also included so conclusion/summary content is available.
    """
    import re
    from ..services.vector_store import merge_rrf_result_lists

    vector_store = _get_vector_store()
    bm25_retriever = _get_bm25_retriever()

    if chunk_count is not None and chunk_count > 1:
        first_chunk_stmt = (
            select(Chunk).where(Chunk.document_id == document_id, Chunk.index == 0)
        )
        first_chunk_result = await db.execute(first_chunk_stmt)
        first_chunk_row = first_chunk_result.scalar_one_or_none()
        theme_snippet = ""
        if first_chunk_row and first_chunk_row.sanitized_text:
            theme_snippet = (first_chunk_row.sanitized_text or "")[:THEME_SNIPPET_LEN]
        if theme_snippet.strip():
            fetch_top_k = min(top_k * 2, 50)
            results_user, results_theme = await asyncio.gather(
                asyncio.to_thread(
                    vector_store.hybrid_search,
                    document_id=document_id,
                    query=query,
                    top_k=fetch_top_k,
                    bm25_retriever=bm25_retriever,
                    alpha=0.5,
                    beta=0.5,
                ),
                asyncio.to_thread(
                    vector_store.hybrid_search,
                    document_id=document_id,
                    query=theme_snippet,
                    top_k=fetch_top_k,
                    bm25_retriever=bm25_retriever,
                    alpha=0.5,
                    beta=0.5,
                ),
            )
            results = merge_rrf_result_lists([results_user, results_theme], top_k)
        else:
            results = await asyncio.to_thread(
                vector_store.hybrid_search,
                document_id=document_id,
                query=query,
                top_k=top_k,
                bm25_retriever=bm25_retriever,
                alpha=0.5,
                beta=0.5,
            )
    else:
        results = await asyncio.to_thread(
            vector_store.hybrid_search,
            document_id=document_id,
            query=query,
            top_k=top_k,
            bm25_retriever=bm25_retriever,
            alpha=0.5,
            beta=0.5,
        )

    used_vector_index = bool(results)

    if results:
        chunk_ids = [cid for cid, _ in results]

        # Fetch initially retrieved chunks.
        stmt = select(Chunk).where(Chunk.id.in_(chunk_ids))
        db_result = await db.execute(stmt)
        base_chunks = list(db_result.scalars().all())

        # Derive numeric section prefixes (e.g., "2." from "2.4.") in a language-agnostic way.
        section_prefixes: set[str] = set()
        for c in base_chunks:
            if not c.section_title:
                continue
            match = re.match(r"^(\d+)\.", c.section_title.strip())
            if match:
                section_prefixes.add(match.group(1))

        # If no numeric prefixes are present, fall back to the base chunks only.
        if not section_prefixes:
            chunks = base_chunks
        else:
            # Fetch all chunks for the document once and expand the retrieval set
            # with any chunks that share the same top-level numeric section prefix.
            all_stmt = select(Chunk).where(Chunk.document_id == document_id)
            all_result = await db.execute(all_stmt)
            all_chunks = list(all_result.scalars().all())

            expanded_chunks: dict[int, Chunk] = {c.id: c for c in base_chunks}
            for c in all_chunks:
                if not c.section_title:
                    continue
                match = re.match(r"^(\d+)\.", c.section_title.strip())
                if match and match.group(1) in section_prefixes:
                    expanded_chunks.setdefault(c.id, c)

            # Sort expanded set by document index to keep section ordering natural.
            chunks = sorted(expanded_chunks.values(), key=lambda c: c.index)
    else:
        fallback_stmt = (
            select(Chunk)
            .where(Chunk.document_id == document_id)
            .order_by(Chunk.index)
            .limit(top_k)
        )
        db_result = await db.execute(fallback_stmt)
        chunks = list(db_result.scalars().all())
    if len(chunks) > 1:
        first_stmt = (
            select(Chunk).where(Chunk.document_id == document_id, Chunk.index == 0)
        )
        first_result = await db.execute(first_stmt)
        first_chunk = first_result.scalar_one_or_none()
        if first_chunk and first_chunk.id is not None and first_chunk.id not in (c.id for c in chunks):
            chunks = [first_chunk] + [c for c in chunks if c.id != first_chunk.id][: top_k - 1]

    if chunk_count is not None and chunk_count >= MIN_CHUNKS_FOR_LAST:
        last_index = chunk_count - 1
        last_stmt = (
            select(Chunk).where(
                Chunk.document_id == document_id,
                Chunk.index == last_index,
            )
        )
        last_result = await db.execute(last_stmt)
        last_chunk = last_result.scalar_one_or_none()
        chunk_ids = {c.id for c in chunks}
        if last_chunk and last_chunk.id is not None and last_chunk.id not in chunk_ids:
            chunks = chunks + [last_chunk]
            chunks = sorted(chunks, key=lambda c: c.index)[:top_k]

    return chunks


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
    document_id: Optional[int] = None,
    query_has_placeholder: bool = False,
    query_placeholders: Optional[List[str]] = None,
    http_request: Optional[Request] = None,
    used_ollama_fallback_ref: Optional[List[bool]] = None,
) -> str:
    """Call the LLM and apply local de-anonymization."""
    llm = LLMRouter(settings)

    async def on_cloud_failure(message: str, extra: dict[str, Any]) -> None:
        if used_ollama_fallback_ref is not None:
            used_ollama_fallback_ref[0] = True
        if http_request is not None:
            try:
                await log_backend_message(db, http_request, message, level="ERROR", extra=extra)
            except Exception:  # noqa: BLE001
                pass

    schema_instruction = ""
    if document_id is not None:
        schema_result = await db.execute(
            select(SpreadsheetSchema)
            .options(selectinload(SpreadsheetSchema.columns))
            .where(SpreadsheetSchema.document_id == document_id)
        )
        schema = schema_result.scalar_one_or_none()
        if schema is not None and schema.columns:
            column_descriptions: list[str] = []
            for column in schema.columns:
                label = column.semantic_label or column.technical_label
                description = f"index {column.index} → {label}"
                if column.is_numeric:
                    description += " (numeric)"
                column_descriptions.append(description)
            if column_descriptions:
                schema_instruction = PromptCatalog.spreadsheet_schema_instruction(
                    column_descriptions
                )

    context_text_for_placeholders = ""
    if context_chunks:
        lines_tmp: List[str] = []
        for idx, chunk in enumerate(context_chunks, start=1):
            lines_tmp.append(f"Chunk {idx}:\n{chunk}")
        context_text_for_placeholders = "\n\n".join(lines_tmp)

    placeholders_in_context = _extract_placeholders_from_context(context_text_for_placeholders)

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

    user_prompt = build_chat_prompt(payload)

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

    deanonymizer = Deanonymizer(settings=settings)
    deanonymized = await deanonymizer.deanonymize(masked_answer, anon_map)

    normalizer = TextNormalizer()
    result = await normalizer.normalize(db, deanonymized)
    if session_id is not None:
        set_chat_debug_record(
            session_id=session_id,
            masked_prompt=user_prompt,
            masked_answer=masked_answer,
            final_answer=result,
        )
    if not anon_map.entity_map and _PLACEHOLDER_PATTERN.search(result):
        result += (
            "\n\n(Names and other values could not be loaded. "
            "Set ENCRYPTION_KEY in the backend .env file and re-upload the document.)"
        )
    return result


@router.post(
    "/ask",
    response_class=StreamingResponse,
    status_code=status.HTTP_200_OK,
)
async def chat_ask(
    request: ChatRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Chat endpoint that streams an SSE response for a single turn."""
    message_text = request.message or request.query
    if not message_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Either 'message' or 'query' must be provided.",
        )

    # Determine whether this request is document-backed (RAG) or pure free-text.
    document_ids_list: List[int] = []
    if request.document_ids:
        if len(request.document_ids) == 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="At least one document_id must be provided.",
            )
        document_ids_list = list(request.document_ids)
    elif request.document_id is not None:
        document_ids_list = [request.document_id]
    document_id = document_ids_list[0] if document_ids_list else None

    settings = await _load_settings(db)
    composer = PolicyComposer()
    policy = await composer.compose(db)
    regulation_names = await _active_regulation_names(db)

    documents_by_id: dict[int, Document] = {}
    if document_ids_list:
        for did in document_ids_list:
            documents_by_id[did] = await _load_document(db, did)
    document: Document | None = documents_by_id.get(document_id) if document_id is not None else None

    base_language = (document.language_override or document.detected_language) if document else "en"
    language = await _detect_language(message_text, fallback=base_language)

    if document is not None:
        anon_map = get_document_map(document.id) or AnonymizationMap(
            document_id=document.id,
            language=language,
        )
        map_entries = len(anon_map.entity_map)
        logger.info(
            "chat document_id=%s anon_map_entries=%s deanon_enabled=%s",
            document.id,
            map_entries,
            getattr(settings, "deanon_enabled", True),
        )
        if map_entries == 0 and getattr(settings, "deanon_enabled", True):
            logger.warning(
                "Deanonymization will have no effect: no map for document_id=%s. "
                "Set ENCRYPTION_KEY in .env and re-upload the document so the map can be loaded after restart.",
                document.id,
            )
    else:
        anon_map = AnonymizationMap(document_id=0, language=language)
        logger.info(
            "chat document_id=None anon_map_entries=%s deanon_enabled=%s",
            len(anon_map.entity_map),
            getattr(settings, "deanon_enabled", True),
        )

    sanitized_query, entity_count = await _sanitize_query(
        message_text, language, settings, anon_map, db
    )
    base_top_k = request.top_k or settings.top_k_retrieval
    total_chunk_count = (
        sum(d.chunk_count for d in documents_by_id.values()) if documents_by_id else 0
    )
    use_full_document = (
        total_chunk_count > 0 and total_chunk_count <= FULL_DOCUMENT_CHUNK_THRESHOLD
    )
    top_k = (
        _effective_top_k(base_top_k, total_chunk_count)
        if documents_by_id and not use_full_document
        else base_top_k
    )
    chunks: List[Chunk] = []
    if documents_by_id:
        if use_full_document:
            for doc in sorted(documents_by_id.values(), key=lambda d: d.id):
                stmt = (
                    select(Chunk)
                    .where(Chunk.document_id == doc.id)
                    .order_by(Chunk.index)
                )
                result = await db.execute(stmt)
                chunks.extend(result.scalars().all())
        elif len(documents_by_id) == 1:
            doc = next(iter(documents_by_id.values()))
            chunks = await _retrieve_chunks(
                db=db,
                document_id=doc.id,
                query=sanitized_query,
                top_k=top_k,
                chunk_count=doc.chunk_count,
            )
        else:
            # For multi-document queries, ensure sufficient context per document
            # while keeping total reasonable (max 50 chunks across all documents)
            MIN_PER_DOC = 10
            MAX_TOTAL = 50
            num_docs = len(documents_by_id)
            per_doc_k = max(MIN_PER_DOC, min(MAX_TOTAL // num_docs, top_k // num_docs))
            for doc in documents_by_id.values():
                chunks_doc = await _retrieve_chunks(
                    db=db,
                    document_id=doc.id,
                    query=sanitized_query,
                    top_k=per_doc_k,
                    chunk_count=doc.chunk_count,
                )
                chunks.extend(chunks_doc)

    session_id = request.session_id or uuid4().hex
    gate: ApprovalGate = get_approval_gate()
    require_approval = (
        request.require_approval
        if request.require_approval is not None
        else settings.require_approval
    )

    effective_settings = settings
    if request.deanon_enabled is not None:
        effective_settings = copy.copy(settings)
        effective_settings.deanon_enabled = request.deanon_enabled

    async def event_stream() -> AsyncGenerator[bytes, None]:
        try:
            yield _encode_sse(
                {
                    "type": "meta",
                    "session_id": session_id,
                    "document_id": document.id if document is not None else None,
                    "language": language,
                    "require_approval": require_approval,
                    "retrieved_chunk_count": len(chunks),
                    "active_regulations": regulation_names,
                }
            )

            context_texts: List[str] = []

            sanitized_chunk_texts: List[str] = []
            if documents_by_id and chunks:
                prev_doc_id: Optional[int] = None
                for c in chunks:
                    header = ""
                    if len(documents_by_id) > 1 and c.document_id != prev_doc_id:
                        header = (
                            "--- Document: "
                            + (documents_by_id[c.document_id].original_filename or "")
                            + " ---\n\n"
                        )
                        prev_doc_id = c.document_id
                    chunk_text = c.sanitized_text or ""
                    
                    # Chunks are stored as RAW text in DB (design decision).
                    # Always sanitize at query time with full validation layer.
                    sanitized_chunk, _ = await _sanitize_query(
                        chunk_text,
                        language,
                        settings,
                        anon_map,
                        db,
                    )
                    
                    sanitized_chunk_texts.append(header + sanitized_chunk)

            if require_approval:
                approval_chunks = (
                    _build_approval_chunks(chunks, sanitized_chunk_texts)
                    if (documents_by_id and chunks)
                    else []
                )
                gate.create(
                    session_id=session_id,
                    masked_prompt=sanitized_query,
                    masked_chunks=[c.text for c in approval_chunks],
                    entity_count=entity_count,
                )

                yield _encode_sse(
                    {
                        "type": "approval_required",
                        "session_id": session_id,
                        "masked_prompt": sanitized_query,
                        "chunks": [
                            {
                                "id": c.id,
                                "document_id": c.document_id,
                                "text": c.text,
                                "source_page": c.source_page,
                                "section_title": c.section_title,
                            }
                            for c in approval_chunks
                        ],
                    }
                )

                decision = await gate.wait_for_approval(session_id)

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

                context_texts = [c.text for c in decision.chunks]
            elif documents_by_id and chunks:
                context_texts = sanitized_chunk_texts
            else:
                context_texts = []

            output_mode = (request.output_mode or "chat").strip().lower()
            query_placeholder_matches = list(
                _PLACEHOLDER_PATTERN.finditer(message_text or "")
            )
            query_placeholders = [m.group(0) for m in query_placeholder_matches]
            query_has_placeholder = bool(query_placeholders)
            used_ollama_fallback: List[bool] = [False]
            answer = await _run_llm_and_deanonymize(
                db=db,
                settings=effective_settings,
                anon_map=anon_map,
                sanitized_query=sanitized_query,
                context_chunks=context_texts,
                regulation_names=regulation_names,
                language=language,
                output_mode=output_mode,
                session_id=session_id,
                document_id=document.id if document is not None else None,
                query_has_placeholder=query_has_placeholder,
                query_placeholders=query_placeholders if query_has_placeholder else None,
                http_request=http_request,
                used_ollama_fallback_ref=used_ollama_fallback,
            )
            for piece in _chunk_text(answer, max_chunk_size=256):
                yield _encode_sse({"type": "answer_chunk", "text": piece})

            yield _encode_sse({
                "type": "end",
                "used_ollama_fallback": used_ollama_fallback[0],
            })
        except LLMRouterError as exc:
            try:
                await log_backend_error(db, http_request, exc, status_code=400)
            except Exception:  # noqa: BLE001
                pass
            yield _encode_sse(
                {
                    "type": "error",
                    "message": str(exc),
                }
            )
        except Exception as exc:
            logger.exception("Unhandled exception in chat event stream")
            try:
                await log_backend_error(db, http_request, exc, status_code=500)
            except Exception:  # noqa: BLE001
                pass
            yield _encode_sse(
                {
                    "type": "error",
                    "message": "An unexpected error occurred while processing the chat request.",
                }
            )

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get(
    "/debug/{session_id}",
    response_model=ChatDebugResponse,
    status_code=status.HTTP_200_OK,
)
async def chat_debug(session_id: str) -> ChatDebugResponse:
    """Return masked prompt/answer and final answer for a given chat session.

    This endpoint is intended only for local debugging and never exposes the
    anonymization map or any raw PII. The `masked_prompt` and `masked_answer`
    fields contain only sanitized content with placeholders.
    """
    record: ChatDebugRecord | None = get_chat_debug_record(session_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No debug information found for this session_id.",
        )
    return ChatDebugResponse(
        session_id=record.session_id,
        masked_prompt=record.masked_prompt,
        masked_answer=record.masked_answer,
        final_answer=record.final_answer,
    )


async def _build_rag_prompt_for_desktop(
    db: AsyncSession,
    settings: AppSettings,
    query: str,
    document_ids: list[int],
    top_k: int,
) -> str:
    """Build a RAG-enabled prompt for Desktop Assistant Mode.

    This helper mirrors the Cloud LLM RAG flow: retrieve chunks, sanitize query and
    context, build ChatContextPayload, and render the final prompt using
    build_chat_prompt. The result is a single string suitable for sending to a
    desktop assistant application.
    """    
    from ..models.regulation import RegulationRuleset

    # Detect language using existing helper
    language = await _detect_language(query, fallback="en")
    # Retrieve chunks and sanitize
    try:
        # Compose active policy (includes validation layer)
        policy_composer = PolicyComposer()
        policy = await policy_composer.compose(db)
        overrides = getattr(settings, "ner_model_overrides", None) or {}
        ner_registry = NERModelRegistry(_overrides=dict(overrides))
        sanitizer = PIISanitizer(
            settings=settings,
            policy=policy,
            ner_registry=ner_registry,
            enable_ollama_layer=True,  # Enable validation layer at query time
        )
        sanitized_query = query  # Default: use original if no documents
        
        if not document_ids:
            # No documents: empty context
            context_chunks: list[str] = []
        else:
            # Load all documents
            documents_by_id: dict[int, Document] = {}
            for did in document_ids:
                doc = await _load_document(db, did)
                if doc:
                    documents_by_id[did] = doc
            
            # Retrieve chunks using the same logic as Cloud LLM flow
            chunks: List[Chunk] = []
            if len(documents_by_id) == 1:
                doc = next(iter(documents_by_id.values()))
                chunks = await _retrieve_chunks(
                    db=db,
                    document_id=doc.id,
                    query=query,
                    top_k=top_k,
                    chunk_count=doc.chunk_count,
                )
            elif len(documents_by_id) > 1:
                MIN_PER_DOC = 10
                MAX_TOTAL = 50
                num_docs = len(documents_by_id)
                per_doc_k = max(MIN_PER_DOC, min(MAX_TOTAL // num_docs, top_k // num_docs))
                for doc in documents_by_id.values():
                    chunks_doc = await _retrieve_chunks(
                        db=db,
                        document_id=doc.id,
                        query=query,
                        top_k=per_doc_k,
                        chunk_count=doc.chunk_count,
                    )
                    chunks.extend(chunks_doc)
            
            # Sanitize query + chunks
            anon_map = AnonymizationMap(document_id=document_ids[0], language=language)
            
            # Sanitize the query
            sanitized_query_result = await asyncio.to_thread(
                sanitizer.sanitize, query, language, anon_map
            )
            sanitized_query = sanitized_query_result.sanitized_text
            
            # Chunks are stored as RAW text in DB (design decision).
            # Always sanitize at query time with full validation layer.
            sanitized_chunks: list[str] = []
            for chunk in chunks:
                chunk_text = chunk.sanitized_text or ""
                if not chunk_text.strip():
                    continue
                
                # Sanitize chunk with full pipeline (includes validation layer)
                sanitized_chunk_result = await asyncio.to_thread(
                    sanitizer.sanitize, chunk_text, language, anon_map
                )
                sanitized_chunks.append(sanitized_chunk_result.sanitized_text)
            context_chunks = sanitized_chunks
    except Exception as exc:
        raise

    # Get active regulations
    try:
        active_regs_result = await db.execute(
            select(RegulationRuleset).where(RegulationRuleset.is_active == True)  # noqa: E712
        )
        active_regs = active_regs_result.scalars().all()
        regulation_names = [reg.display_name for reg in active_regs]
    except Exception as exc:
        raise

    # Build payload (no schema instruction for Desktop Assistant)
    try:
        payload_ctx = ChatContextPayload(
            sanitized_query=sanitized_query,
            context_chunks=context_chunks,
            regulation_names=regulation_names,
            language=language,
            schema_instruction="",
            placeholder_list_str="",
            output_mode="chat",
        )
        
        final_prompt = build_chat_prompt(payload_ctx)
        
        return final_prompt
    except Exception as exc:
        raise


@router.post(
    "/desktop-assistant/send",
    response_model=DesktopAssistantResponse,
    status_code=status.HTTP_200_OK,
)
async def desktop_assistant_send(
    payload: DesktopAssistantRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Send a message directly to a local desktop assistant client.

    When `use_rag=True`, this endpoint retrieves document chunks, sanitizes them
    (including the query), and constructs a RAG-enabled prompt using the same logic
    as the Cloud LLM flow. The resulting prompt is sent to the desktop assistant
    via OS automation. The user then sees the prompt in the desktop assistant's
    input field and can submit it manually or modify it first.

    When `use_rag=False`, the raw user query is sent directly to the desktop assistant
    without any document context or sanitization.
    """

    settings = await _load_settings(db)
    if not getattr(settings, "desktop_assistant_enabled", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Desktop assistant mode is disabled in settings.",
        )

    if not payload.message.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Message must not be empty.",
        )

    try:
        assistant = create_desktop_assistant(settings)
    except Exception as exc:  # noqa: BLE001
        # Only log metadata, never the raw message.
        await log_backend_error(
            db,
            http_request,
            exc,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=DesktopAssistantResponse(
                status="error",
                message="Desktop assistant is not available on this platform.",
            ).model_dump(),
        )

    # Determine final message to send
    final_message = payload.message
    
    if payload.use_rag:
        # Build RAG-enabled prompt using the same logic as Cloud LLM
        try:
            final_message = await _build_rag_prompt_for_desktop(
                db=db,
                settings=settings,
                query=payload.message,
                document_ids=payload.document_ids,
                top_k=payload.top_k,
            )
        except Exception as exc:  # noqa: BLE001
            await log_backend_error(
                db,
                http_request,
                exc,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                extra={
                    "rag_enabled": True,
                    "document_ids": payload.document_ids,
                },
            )
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content=DesktopAssistantResponse(
                    status="error",
                    message="Failed to build RAG prompt for desktop assistant.",
                ).model_dump(),
            )

    # Check if approval is required
    require_approval = settings.require_approval and not payload.skip_approval
    if require_approval:
        # Return prompt for user approval (same as Cloud LLM mode)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=DesktopAssistantResponse(
                status="approval_required",
                prompt=final_message,
                requires_approval=True,
                message="Approval required. Please review the prompt before sending.",
            ).model_dump(),
        )

    try:
        # Run potentially blocking OS automation in a worker thread.
        await asyncio.to_thread(
            assistant.send_message,
            final_message,
            payload.target,
            payload.open_new_chat,
        )
    except DesktopAssistantError as exc:
        await log_backend_error(
            db,
            http_request,
            exc,
            status_code=status.HTTP_400_BAD_REQUEST,
            extra={
                "desktop_assistant_target": payload.target.value,
            },
        )
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=DesktopAssistantResponse(
                status="error",
                message=str(exc),
            ).model_dump(),
        )
    except Exception as exc:  # noqa: BLE001
        await log_backend_error(
            db,
            http_request,
            exc,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            extra={
                "desktop_assistant_target": payload.target.value,
            },
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=DesktopAssistantResponse(
                status="error",
                message="Failed to send message to desktop assistant.",
            ).model_dump(),
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=DesktopAssistantResponse(status="ok").model_dump(),
    )

