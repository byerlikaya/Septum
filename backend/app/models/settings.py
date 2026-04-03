from __future__ import annotations

"""Application-wide settings stored in the database."""

from typing import Any, Dict, List, Optional

from sqlalchemy import Boolean, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


class AppSettings(Base):
    """Represents global application configuration for Septum."""

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True)

    llm_provider: Mapped[str] = mapped_column(String, nullable=False)
    llm_model: Mapped[str] = mapped_column(String, nullable=False)
    ollama_base_url: Mapped[str] = mapped_column(String, nullable=False)
    ollama_chat_model: Mapped[str] = mapped_column(String, nullable=False)
    ollama_deanon_model: Mapped[str] = mapped_column(String, nullable=False)

    deanon_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    deanon_strategy: Mapped[str] = mapped_column(String, nullable=False)
    require_approval: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    show_json_output: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    use_presidio_layer: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    use_ner_layer: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    use_ollama_validation_layer: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    use_ollama_layer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_overlap: Mapped[int] = mapped_column(Integer, nullable=False)
    top_k_retrieval: Mapped[int] = mapped_column(Integer, nullable=False)
    pdf_chunk_size: Mapped[int] = mapped_column(Integer, nullable=False)
    audio_chunk_size: Mapped[int] = mapped_column(Integer, nullable=False)
    spreadsheet_chunk_size: Mapped[int] = mapped_column(Integer, nullable=False)

    whisper_model: Mapped[str] = mapped_column(String, nullable=False)
    default_audio_language: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )
    image_ocr_languages: Mapped[List[str]] = mapped_column(JSON, nullable=False)
    ocr_provider: Mapped[str] = mapped_column(String, nullable=False)
    ocr_provider_options: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON, nullable=True
    )
    extract_embedded_images: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    recursive_email_attachments: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )

    default_active_regulations: Mapped[List[str]] = mapped_column(
        JSON, nullable=False
    )

    ner_model_overrides: Mapped[Optional[Dict[str, str]]] = mapped_column(
        JSON, nullable=True
    )

