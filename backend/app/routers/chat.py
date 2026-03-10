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

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from langdetect import DetectorFactory, LangDetectException, detect
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.document import Chunk, Document
from ..models.regulation import RegulationRuleset
from ..models.settings import AppSettings
from ..services.anonymization_map import AnonymizationMap
from ..services.approval_gate import ApprovalChunk, ApprovalGate, get_approval_gate
from ..services.document_anon_store import get_document_map
from ..services.deanonymizer import Deanonymizer
from ..services.llm_router import LLMRouter, LLMRouterError, _chunk_text
from ..services.sanitizer import PIISanitizer


router = APIRouter(prefix="/api/chat", tags=["chat"])


# Make langdetect deterministic.
DetectorFactory.seed = 42


class ChatRequest(BaseModel):
    """Incoming chat request from the frontend.

    This model supports both the newer v5-style payload
    (``message``, ``document_id``) and the legacy STEP 14
    payload shape (``query``, ``document_ids``, etc.).
    """

    # New v5-style fields
    message: Optional[str] = None
    document_id: Optional[int] = None
    top_k: Optional[int] = None
    session_id: Optional[str] = None

    # Legacy v4/STEP 14 fields for backwards compatibility
    query: Optional[str] = None
    document_ids: Optional[List[int]] = None
    output_mode: Optional[str] = "chat"
    require_approval: Optional[bool] = None
    deanon_enabled: Optional[bool] = None


async def _load_settings(db: AsyncSession) -> AppSettings:
    result = await db.execute(select(AppSettings).where(AppSettings.id == 1))
    settings = result.scalar_one_or_none()
    if settings is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Application settings have not been initialized.",
        )
    # Allow environment variables to override selected runtime settings
    # without requiring a database migration.
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
) -> tuple[str, int]:
    """Run the sanitizer on the incoming user message."""
    sanitizer = PIISanitizer(settings=settings)
    result = await asyncio.to_thread(
        sanitizer.sanitize,
        message,
        language,
        anon_map,
    )
    return result.sanitized_text, result.entity_count


async def _retrieve_chunks(
    db: AsyncSession,
    document_id: int,
    query: str,
    top_k: int,
) -> List[Chunk]:
    """Retrieve top-k chunks for a sanitized query using FAISS indexes.

    If the vector index is missing or returns no results, falls back to the
    first top_k chunks of the document (by chunk index) so the LLM still
    receives context when the document has chunks.
    """
    from ..services.vector_store import VectorStore

    vector_store = VectorStore()
    results = vector_store.search(
        document_id=document_id,
        query=query,
        top_k=top_k,
    )

    if results:
        chunk_ids = [cid for cid, _ in results]
        id_to_rank = {cid: rank for rank, cid in enumerate(chunk_ids)}
        stmt = select(Chunk).where(Chunk.id.in_(chunk_ids))
        db_result = await db.execute(stmt)
        chunks = list(db_result.scalars().all())
        chunks.sort(key=lambda c: id_to_rank.get(c.id, len(chunk_ids)))
        return chunks

    # Fallback: no index or empty search; use first top_k chunks by order
    fallback_stmt = (
        select(Chunk)
        .where(Chunk.document_id == document_id)
        .order_by(Chunk.index)
        .limit(top_k)
    )
    db_result = await db.execute(fallback_stmt)
    return list(db_result.scalars().all())


def _build_approval_chunks(chunks: List[Chunk]) -> List[ApprovalChunk]:
    """Convert database chunks into ApprovalChunk payloads."""
    approval_chunks: List[ApprovalChunk] = []
    for c in chunks:
        approval_chunks.append(
            ApprovalChunk(
                id=c.id,
                document_id=c.document_id,
                text=c.sanitized_text,
                source_page=c.source_page,
                source_slide=c.source_slide,
                source_sheet=c.source_sheet,
                source_timestamp_start=c.source_timestamp_start,
                source_timestamp_end=c.source_timestamp_end,
                section_title=c.section_title,
            )
        )
    return approval_chunks


# Placeholder format: [ENTITY_TYPE_N] e.g. [PERSON_1], [ORGANIZATION_2]
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
    settings: AppSettings,
    anon_map: AnonymizationMap,
    sanitized_query: str,
    context_chunks: List[str],
    regulation_names: List[str],
    output_mode: str = "chat",
) -> str:
    """Call the LLM and apply local de-anonymization."""
    llm = LLMRouter(settings)

    regulations_str = ", ".join(regulation_names) if regulation_names else "None"
    context_text = ""
    if context_chunks:
        lines: List[str] = []
        for idx, chunk in enumerate(context_chunks, start=1):
            lines.append(f"Chunk {idx}:\n{chunk}")
        context_text = "\n\n".join(lines)

    placeholders_in_context = _extract_placeholders_from_context(context_text)

    placeholder_list_str = ""
    if placeholders_in_context:
        placeholder_list_str = (
            "\n\nThe user question may be in any language (e.g. Turkish, English). "
            "Interpret it by intent: if they are asking which persons, organizations, or "
            "other named entities appear or are mentioned in the document, reply with ONLY "
            "a bullet list of these placeholder tokens (one per line): "
            + ", ".join(placeholders_in_context)
            + ". Do not list document wording, clause fragments, or any other text—only "
            "the tokens above. Do not say you cannot answer or that something is \"not defined\"; "
            "answer with the token list when the question is about who/what entities are in the document. "
            "For any other question, answer using the context as usual.\n\n"
        )

    output_instruction = ""
    if (output_mode or "chat").strip().lower() == "json":
        output_instruction = (
            "\n\n---\n"
            "REQUIRED: Reply with ONLY a single valid JSON object. No markdown headings, no bullet lists, "
            "no code fences, no text before or after. Example format:\n"
            '{"summary": "one paragraph", "type": "document type", "key_points": ["point1", "point2"]}\n'
            "Use only double quotes. Output nothing but this JSON object.\n---\n\n"
        )

    # For maximum compatibility across providers (especially Anthropic's
    # Messages API, which expects only ``user`` / ``assistant`` roles),
    # embed the system-style instructions into a single ``user`` message
    # instead of using a separate ``system`` role.
    user_prompt = (
        "You are a privacy-preserving assistant. Personal data in the context is "
        "replaced by placeholders in square brackets (e.g. [PERSON_1], [ORGANIZATION_2]). "
        "Never guess or reconstruct real values.\n\n"
        f"{placeholder_list_str}"
        f"Active privacy regulations: {regulations_str}.\n\n"
        "User question (sanitized):\n"
        f"{sanitized_query}\n\n"
        "Relevant context (sanitized):\n"
        f"{context_text or '[no retrieved context]'}"
        f"{output_instruction}"
    )

    messages = [
        {"role": "user", "content": user_prompt},
    ]

    masked_answer = await llm.complete(messages=messages)

    # If the LLM returned no placeholder tokens but we have placeholders in context,
    # substitute with the actual list when the response is clearly wrong: document
    # fragments (long bullet list) or "I cannot answer" / "not defined" refusals.
    if placeholders_in_context and not _PLACEHOLDER_PATTERN.search(masked_answer):
        refusal_phrases = (
            "cannot answer",
            "not defined",
            "cannot provide",
            "could you clarify",
            "rephrase your question",
        )
        is_refusal = any(p in masked_answer.lower() for p in refusal_phrases)
        bullet_lines = [
            line.strip()
            for line in masked_answer.splitlines()
            if line.strip() and line.strip().startswith(("•", "-", "*"))
        ]
        if is_refusal or len(bullet_lines) >= 5:
            masked_answer = "\n".join(f"- {p}" for p in placeholders_in_context)

    deanonymizer = Deanonymizer(settings=settings)
    result = await deanonymizer.deanonymize(masked_answer, anon_map)
    if not anon_map.entity_map and _PLACEHOLDER_PATTERN.search(result):
        result += (
            "\n\n(İsimler ve diğer değerler yüklenemedi. "
            "Backend .env dosyasında ENCRYPTION_KEY tanımlayıp belgeyi yeniden yükleyin.)"
        )
    return result


@router.post(
    "/ask",
    response_class=StreamingResponse,
    status_code=status.HTTP_200_OK,
)
async def chat_ask(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Chat endpoint that streams an SSE response for a single turn."""
    # Support both new (message, document_id) and legacy
    # (query, document_ids) payload shapes.
    message_text = request.message or request.query
    if not message_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Either 'message' or 'query' must be provided.",
        )

    if request.document_id is not None:
        document_id = request.document_id
    elif request.document_ids:
        if len(request.document_ids) == 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="At least one document_id must be provided.",
            )
        # For now, support a single document by taking the first ID.
        document_id = request.document_ids[0]
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Either 'document_id' or 'document_ids' must be provided.",
        )

    settings = await _load_settings(db)
    document = await _load_document(db, document_id)
    regulation_names = await _active_regulation_names(db)

    # Prefer per-document language override if present; otherwise, detect from
    # the incoming query text as a best-effort refinement.
    base_language = document.language_override or document.detected_language
    language = await _detect_language(message_text, fallback=base_language)

    # Use document's in-memory map so we can deanonymize the LLM answer.
    anon_map = get_document_map(document.id) or AnonymizationMap(
        document_id=document.id, language=language
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
    sanitized_query, entity_count = await _sanitize_query(
        message=message_text,
        language=language,
        settings=settings,
        anon_map=anon_map,
    )

    top_k = request.top_k or settings.top_k_retrieval
    chunks = await _retrieve_chunks(
        db=db,
        document_id=document.id,
        query=sanitized_query,
        top_k=top_k,
    )

    session_id = request.session_id or uuid4().hex
    gate: ApprovalGate = get_approval_gate()
    require_approval = (
        request.require_approval
        if request.require_approval is not None
        else settings.require_approval
    )

    # Allow per-request override of de-anonymization without mutating
    # the persisted application settings.
    effective_settings = settings
    if request.deanon_enabled is not None:
        effective_settings = copy.copy(settings)
        effective_settings.deanon_enabled = request.deanon_enabled

    async def event_stream() -> AsyncGenerator[bytes, None]:
        try:
            # Initial metadata event.
            yield _encode_sse(
                {
                    "type": "meta",
                    "session_id": session_id,
                    "document_id": document.id,
                    "language": language,
                    "require_approval": require_approval,
                    "retrieved_chunk_count": len(chunks),
                    "active_regulations": regulation_names,
                }
            )

            context_texts: List[str] = []

            if require_approval and chunks:
                approval_chunks = _build_approval_chunks(chunks)
                # Register the approval session with masked prompt and chunks.
                gate.create(
                    session_id=session_id,
                    masked_prompt=sanitized_query,
                    masked_chunks=[c.text for c in approval_chunks],
                    entity_count=entity_count,
                )

                # Notify the frontend that approval is required.
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
            else:
                context_texts = [c.sanitized_text for c in chunks]

            output_mode = (request.output_mode or "chat").strip().lower()
            answer = await _run_llm_and_deanonymize(
                settings=effective_settings,
                anon_map=anon_map,
                sanitized_query=sanitized_query,
                context_chunks=context_texts,
                regulation_names=regulation_names,
                output_mode=output_mode,
            )

            for piece in _chunk_text(answer, max_chunk_size=256):
                yield _encode_sse({"type": "answer_chunk", "text": piece})

            yield _encode_sse({"type": "end"})
        except LLMRouterError as exc:
            # Surface LLM configuration errors (for example missing API keys)
            # to the client in a controlled, non-PII-leaking manner.
            yield _encode_sse(
                {
                    "type": "error",
                    "message": str(exc),
                }
            )
        except Exception:
            # Avoid leaking internal error details to the client.
            yield _encode_sse(
                {
                    "type": "error",
                    "message": "An unexpected error occurred while processing the chat request.",
                }
            )

    return StreamingResponse(event_stream(), media_type="text/event-stream")

