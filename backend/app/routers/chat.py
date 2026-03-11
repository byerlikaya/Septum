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
from fastapi.responses import StreamingResponse
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
        settings=settings, policy=policy, ner_registry=ner_registry
    )
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

    The first chunk (index 0) is always included when the document has multiple
    chunks, so that document-opening content (e.g. titles, parties, key facts)
    is available regardless of query wording or language.
    """
    from ..services.vector_store import VectorStore

    import re

    vector_store = VectorStore()
    results = vector_store.search(
        document_id=document_id,
        query=query,
        top_k=top_k,
    )

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

    return chunks


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

    regulations_str = ", ".join(regulation_names) if regulation_names else "None"
    context_text = ""
    if context_chunks:
        lines: List[str] = []
        for idx, chunk in enumerate(context_chunks, start=1):
            lines.append(f"Chunk {idx}:\n{chunk}")
        context_text = "\n\n".join(lines)

    placeholders_in_context = _extract_placeholders_from_context(context_text)

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
                schema_instruction = (
                    "Active spreadsheet schema (generic, no raw personal data): "
                    + "; ".join(column_descriptions)
                    + ".\n\n"
                    "When the schema marks a column as a numeric measure, "
                    "and the user asks for aggregate calculations (totals, sums, averages, minimums, maximums, counts), you MUST perform "
                    "the requested calculation over all rows visible in the provided context instead of just repeating a single row. "
                    "Respond with the final numeric result in natural language, without listing every row unless explicitly requested.\n\n"
                )

    placeholder_list_str = ""
    if placeholders_in_context and query_has_placeholder:
        placeholder_list_str = (
            "\n\nThe user question may be in any language. Interpret by intent. "
            "If they ask for a specific piece of information (e.g. a person's name, a date, a single value), "
            "reply with only the placeholder token(s) from the context that directly answer that question. "
            "If they ask which persons, organizations, or other named entities appear in the document, "
            "reply with a bullet list of the relevant placeholder tokens from: "
            + ", ".join(placeholders_in_context)
            + ". Do not list document wording, clause fragments, or other text—only placeholder tokens. "
            "Do not refuse; answer from the context. For any other question, use the context as usual.\n\n"
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

    user_prompt = PromptCatalog.chat_user_prompt(
        language=language,
        regulations_str=regulations_str,
        sanitized_query=sanitized_query,
        context_text=context_text,
        schema_instruction=schema_instruction,
        placeholder_list_str=placeholder_list_str,
        output_instruction=output_instruction,
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
    document_id: Optional[int]
    if request.document_id is not None:
        document_id = request.document_id
    elif request.document_ids:
        if len(request.document_ids) == 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="At least one document_id must be provided.",
            )
        document_id = request.document_ids[0]
    else:
        document_id = None

    settings = await _load_settings(db)
    composer = PolicyComposer()
    policy = await composer.compose(db)
    regulation_names = await _active_regulation_names(db)

    document: Document | None = None
    if document_id is not None:
        document = await _load_document(db, document_id)

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

    sanitized_query = message_text
    entity_count = 0
    top_k = request.top_k or settings.top_k_retrieval
    chunks: List[Chunk] = []
    if document is not None:
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

            if document is not None and require_approval and chunks:
                approval_chunks = _build_approval_chunks(chunks)
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
            elif document is not None:
                context_texts = [c.sanitized_text for c in chunks]
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

