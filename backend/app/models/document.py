from __future__ import annotations

"""ORM models for documents ingested into Septum."""

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import Base


class Document(Base):
    """Represents an uploaded document and its metadata."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    original_filename: Mapped[str] = mapped_column(String, nullable=False)
    file_type: Mapped[str] = mapped_column(String, nullable=False)  # MIME type
    file_format: Mapped[str] = mapped_column(String, nullable=False)
    detected_language: Mapped[str] = mapped_column(String, nullable=False)
    language_override: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    encrypted_path: Mapped[str] = mapped_column(String, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    entity_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ingestion_status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    ingestion_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    transcription_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ocr_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    active_regulation_ids: Mapped[List[str]] = mapped_column(
        JSON, nullable=False, default=list
    )

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Chunk(Base):
    """Represents a text chunk derived from a document.
    
    Chunks can be either clause-based (free-form text from sections/paragraphs)
    or field-based (structured key-value pairs extracted from tables/forms).
    """

    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    sanitized_text: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_page: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_slide: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_sheet: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_timestamp_start: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source_timestamp_end: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    section_title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    chunk_type: Mapped[str] = mapped_column(
        String, nullable=False, default="clause"
    )
    field_label: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    field_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    field_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    document: Mapped[Document] = relationship(back_populates="chunks")

