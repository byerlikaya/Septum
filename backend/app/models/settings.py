from __future__ import annotations

"""Application-wide settings stored in the database."""

from typing import List

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


class AppSettings(Base):
    """Represents global application configuration for Septum."""

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True)

    # LLM configuration
    llm_provider: Mapped[str] = mapped_column(String, nullable=False)
    llm_model: Mapped[str] = mapped_column(String, nullable=False)
    ollama_base_url: Mapped[str] = mapped_column(String, nullable=False)
    ollama_chat_model: Mapped[str] = mapped_column(String, nullable=False)
    ollama_deanon_model: Mapped[str] = mapped_column(String, nullable=False)

    # De-anonymization & approval
    deanon_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    deanon_strategy: Mapped[str] = mapped_column(String, nullable=False)
    require_approval: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    show_json_output: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Sanitization layers
    use_presidio_layer: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    use_ner_layer: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    use_ollama_layer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # RAG / chunking
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_overlap: Mapped[int] = mapped_column(Integer, nullable=False)
    top_k_retrieval: Mapped[int] = mapped_column(Integer, nullable=False)
    pdf_chunk_size: Mapped[int] = mapped_column(Integer, nullable=False)
    audio_chunk_size: Mapped[int] = mapped_column(Integer, nullable=False)
    spreadsheet_chunk_size: Mapped[int] = mapped_column(Integer, nullable=False)

    # Ingestion / models
    whisper_model: Mapped[str] = mapped_column(String, nullable=False)
    image_ocr_languages: Mapped[List[str]] = mapped_column(JSON, nullable=False)
    extract_embedded_images: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    recursive_email_attachments: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )

    # Regulations
    default_active_regulations: Mapped[List[str]] = mapped_column(
        JSON, nullable=False
    )

