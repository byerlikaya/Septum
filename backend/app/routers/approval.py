from __future__ import annotations

"""FastAPI router for user approval of retrieved chunks.

This router exposes a minimal HTTP surface around :mod:`services.approval_gate`
so that the frontend can:

* Submit edited chunks for approval.
* Explicitly reject a pending approval request.
* Optionally poll the current status of an approval session.
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..services.approval_gate import (
    ApprovalChunk,
    ApprovalDecision,
    ApprovalGate,
    ApprovalSessionNotFoundError,
    get_approval_gate,
)

router = APIRouter(prefix="/api/approval", tags=["approval"])


class ApprovalChunkPayload(BaseModel):
    """Payload representation of a sanitized chunk in the approval modal."""

    id: Optional[int] = Field(default=None)
    document_id: Optional[int] = Field(default=None)
    text: str
    source_page: Optional[int] = Field(default=None)
    source_slide: Optional[int] = Field(default=None)
    source_sheet: Optional[int] = Field(default=None)
    source_timestamp_start: Optional[float] = Field(default=None)
    source_timestamp_end: Optional[float] = Field(default=None)
    section_title: Optional[str] = Field(default=None)

    def to_domain(self) -> ApprovalChunk:
        """Convert the payload into a domain-level :class:`ApprovalChunk`."""
        return ApprovalChunk(
            id=self.id,
            document_id=self.document_id,
            text=self.text,
            source_page=self.source_page,
            source_slide=self.source_slide,
            source_sheet=self.source_sheet,
            source_timestamp_start=self.source_timestamp_start,
            source_timestamp_end=self.source_timestamp_end,
            section_title=self.section_title,
        )

    @classmethod
    def from_domain(cls, chunk: ApprovalChunk) -> "ApprovalChunkPayload":
        """Create a payload model from a domain-level :class:`ApprovalChunk`."""
        return cls(
            id=chunk.id,
            document_id=chunk.document_id,
            text=chunk.text,
            source_page=chunk.source_page,
            source_slide=chunk.source_slide,
            source_sheet=chunk.source_sheet,
            source_timestamp_start=chunk.source_timestamp_start,
            source_timestamp_end=chunk.source_timestamp_end,
            section_title=chunk.section_title,
        )


class ApprovalDecisionResponse(BaseModel):
    """API response describing the outcome of an approval session."""

    session_id: str
    approved: bool
    timed_out: bool
    reason: Optional[str] = None
    chunks: List[ApprovalChunkPayload]

    @classmethod
    def from_domain(
        cls,
        session_id: str,
        decision: ApprovalDecision,
    ) -> "ApprovalDecisionResponse":
        return cls(
            session_id=session_id,
            approved=decision.approved,
            timed_out=decision.timed_out,
            reason=decision.reason,
            chunks=[ApprovalChunkPayload.from_domain(c) for c in decision.chunks],
        )


class ApprovalSessionStatus(BaseModel):
    """Lightweight status view for an approval session."""

    session_id: str
    created_at: datetime
    has_decision: bool
    approved: Optional[bool] = None
    timed_out: bool = False


class ApproveRequest(BaseModel):
    """Request body for approving a pending session."""

    chunks: List[ApprovalChunkPayload]


class RejectRequest(BaseModel):
    """Request body for rejecting a pending session."""

    reason: Optional[str] = None


class PreviewPromptRequest(BaseModel):
    """Request body for re-assembling the user prompt with edited chunks."""

    chunks: List[ApprovalChunkPayload]


class PreviewPromptResponse(BaseModel):
    """Response body carrying the rebuilt masked user prompt."""

    session_id: str
    assembled_prompt: str


def _get_gate() -> ApprovalGate:
    return get_approval_gate()


@router.get(
    "/{session_id}",
    response_model=ApprovalSessionStatus,
    status_code=status.HTTP_200_OK,
)
async def get_session_status(session_id: str) -> ApprovalSessionStatus:
    """Return basic status information for an approval session.

    This endpoint is intended primarily for debugging and optional UI polling.
    The primary data flow of chunks to the frontend remains the chat SSE
    stream; the approval router simply coordinates the user's decision.
    """
    gate = _get_gate()
    session = await gate.get_session_snapshot(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval session not found.",
        )

    return ApprovalSessionStatus(
        session_id=session.session_id,
        created_at=session.created_at,
        has_decision=session.decision is not None,
        approved=session.decision.approved if session.decision is not None else None,
        timed_out=bool(session.decision.timed_out) if session.decision is not None else False,
    )


@router.post(
    "/{session_id}/approve",
    response_model=ApprovalDecisionResponse,
    status_code=status.HTTP_200_OK,
)
async def approve_session(
    session_id: str,
    request: ApproveRequest,
) -> ApprovalDecisionResponse:
    """Approve an existing session with the (possibly edited) chunks."""
    gate = _get_gate()
    try:
        decision = await gate.approve(
            session_id=session_id,
            edited_chunks=[payload.to_domain() for payload in request.chunks],
        )
    except ApprovalSessionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval session not found.",
        ) from None

    return ApprovalDecisionResponse.from_domain(session_id, decision)


@router.post(
    "/{session_id}/reject",
    response_model=ApprovalDecisionResponse,
    status_code=status.HTTP_200_OK,
)
async def reject_session(
    session_id: str,
    request: RejectRequest,
) -> ApprovalDecisionResponse:
    """Explicitly reject an existing approval session."""
    gate = _get_gate()
    try:
        decision = await gate.reject(session_id=session_id, reason=request.reason)
    except ApprovalSessionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval session not found.",
        ) from None

    return ApprovalDecisionResponse.from_domain(session_id, decision)


@router.post(
    "/{session_id}/preview-prompt",
    response_model=PreviewPromptResponse,
    status_code=status.HTTP_200_OK,
)
async def preview_prompt(
    session_id: str,
    request: PreviewPromptRequest,
    db: AsyncSession = Depends(get_db),
) -> PreviewPromptResponse:
    """Re-assemble the masked user prompt with (possibly edited) chunks.

    This endpoint is called by the frontend approval modal whenever the
    user edits a chunk, so the "Bulut LLM'e gönderilecek tam prompt" preview
    stays byte-for-byte in sync with what would actually be sent to the
    cloud LLM. It looks up the original assembly context (sanitized query,
    regulation list, language, output mode, etc.) from the in-memory
    approval session, swaps the chunks, and runs the exact same
    ``_assemble_user_prompt`` helper the chat handler uses.
    """
    # Imported lazily to avoid a circular import between the chat router
    # (which depends on approval_gate) and this router (which now wants to
    # share the chat router's prompt-assembly helper).
    from .chat import _assemble_user_prompt

    gate = _get_gate()
    context = await gate.get_assembly_context(session_id)
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Approval session not found or missing assembly context; "
                "cannot rebuild the prompt preview."
            ),
        )

    assembled = await _assemble_user_prompt(
        db=db,
        sanitized_query=context.get("sanitized_query", ""),
        context_chunks=[c.text for c in request.chunks],
        regulation_names=list(context.get("regulation_names", []) or []),
        language=context.get("language", "en"),
        output_mode=context.get("output_mode", "chat"),
        document_id=context.get("document_id"),
        query_has_placeholder=bool(context.get("query_has_placeholder", False)),
    )

    return PreviewPromptResponse(
        session_id=session_id,
        assembled_prompt=assembled,
    )

