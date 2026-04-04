from __future__ import annotations

"""FastAPI router for managing persistent chat sessions."""

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..database import get_db
from ..models.chat_session import ChatSession, ChatMessage

router = APIRouter(prefix="/api/chat-sessions", tags=["chat-sessions"])


class ChatMessageResponse(BaseModel):
    """Serialized view of a single chat message."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    created_at: datetime


class ChatSessionResponse(BaseModel):
    """Serialized view of a chat session (list context, no messages)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    created_at: datetime
    updated_at: datetime
    document_ids: Optional[List[int]] = None
    message_count: int = 0


class ChatSessionDetailResponse(ChatSessionResponse):
    """Serialized view of a chat session with messages."""

    messages: List[ChatMessageResponse] = []


class CreateSessionPayload(BaseModel):
    """Payload for creating a new chat session."""

    title: Optional[str] = None
    document_ids: Optional[List[int]] = None


class UpdateSessionPayload(BaseModel):
    """Payload for updating a chat session."""

    title: Optional[str] = None
    document_ids: Optional[List[int]] = None


class AddMessagePayload(BaseModel):
    """Payload for adding a message to a session."""

    role: str
    content: str


@router.get("", response_model=List[ChatSessionResponse])
async def list_sessions(db: AsyncSession = Depends(get_db)) -> list:
    """List all chat sessions ordered by most recently updated."""
    stmt = (
        select(
            ChatSession,
            func.count(ChatMessage.id).label("message_count"),
        )
        .outerjoin(ChatMessage)
        .group_by(ChatSession.id)
        .order_by(ChatSession.updated_at.desc())
    )
    rows = (await db.execute(stmt)).all()
    results = []
    for session, msg_count in rows:
        results.append(
            ChatSessionResponse(
                id=session.id,
                title=session.title,
                created_at=session.created_at,
                updated_at=session.updated_at,
                document_ids=session.document_ids,
                message_count=msg_count,
            )
        )
    return results


@router.post("", response_model=ChatSessionDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: CreateSessionPayload,
    db: AsyncSession = Depends(get_db),
) -> ChatSessionDetailResponse:
    """Create a new chat session."""
    now = datetime.now(timezone.utc)
    session = ChatSession(
        title=payload.title or "New Chat",
        created_at=now,
        updated_at=now,
        document_ids=payload.document_ids,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return ChatSessionDetailResponse(
        id=session.id,
        title=session.title,
        created_at=session.created_at,
        updated_at=session.updated_at,
        document_ids=session.document_ids,
        message_count=0,
        messages=[],
    )


@router.get("/{session_id}", response_model=ChatSessionDetailResponse)
async def get_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
) -> ChatSessionDetailResponse:
    """Get a chat session with all its messages."""
    stmt = (
        select(ChatSession)
        .where(ChatSession.id == session_id)
        .options(selectinload(ChatSession.messages))
    )
    session = (await db.execute(stmt)).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return ChatSessionDetailResponse(
        id=session.id,
        title=session.title,
        created_at=session.created_at,
        updated_at=session.updated_at,
        document_ids=session.document_ids,
        message_count=len(session.messages),
        messages=[
            ChatMessageResponse(
                id=m.id, role=m.role, content=m.content, created_at=m.created_at
            )
            for m in session.messages
        ],
    )


@router.patch("/{session_id}", response_model=ChatSessionResponse)
async def update_session(
    session_id: int,
    payload: UpdateSessionPayload,
    db: AsyncSession = Depends(get_db),
) -> ChatSessionResponse:
    """Update a chat session's title or document_ids."""
    session = await db.get(ChatSession, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if payload.title is not None:
        session.title = payload.title
    if payload.document_ids is not None:
        session.document_ids = payload.document_ids
    session.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(session)
    msg_count = (
        await db.execute(
            select(func.count(ChatMessage.id)).where(ChatMessage.session_id == session_id)
        )
    ).scalar() or 0
    return ChatSessionResponse(
        id=session.id,
        title=session.title,
        created_at=session.created_at,
        updated_at=session.updated_at,
        document_ids=session.document_ids,
        message_count=msg_count,
    )


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a chat session and all its messages."""
    session = await db.get(ChatSession, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    await db.delete(session)
    await db.commit()


@router.post("/{session_id}/messages", response_model=ChatMessageResponse, status_code=status.HTTP_201_CREATED)
async def add_message(
    session_id: int,
    payload: AddMessagePayload,
    db: AsyncSession = Depends(get_db),
) -> ChatMessageResponse:
    """Add a message to a chat session."""
    session = await db.get(ChatSession, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    msg = ChatMessage(
        session_id=session_id,
        role=payload.role,
        content=payload.content,
        created_at=datetime.now(timezone.utc),
    )
    db.add(msg)
    session.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(msg)
    return ChatMessageResponse(
        id=msg.id, role=msg.role, content=msg.content, created_at=msg.created_at
    )
