"""Initial schema — all existing Septum tables.

Revision ID: 001
Revises: None
Create Date: 2026-04-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- documents ---
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("filename", sa.String, nullable=False),
        sa.Column("original_filename", sa.String, nullable=False),
        sa.Column("file_type", sa.String, nullable=False),
        sa.Column("file_format", sa.String, nullable=False),
        sa.Column("detected_language", sa.String, nullable=False),
        sa.Column("language_override", sa.String, nullable=True),
        sa.Column("uploaded_at", sa.DateTime, nullable=False),
        sa.Column("encrypted_path", sa.String, nullable=False),
        sa.Column("chunk_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("entity_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("ingestion_status", sa.String, nullable=False, server_default="pending"),
        sa.Column("ingestion_error", sa.Text, nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger, nullable=False),
        sa.Column("transcription_text", sa.Text, nullable=True),
        sa.Column("ocr_confidence", sa.Float, nullable=True),
        sa.Column("active_regulation_ids", sa.JSON, nullable=False),
    )

    # --- chunks ---
    op.create_table(
        "chunks",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "document_id",
            sa.Integer,
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("index", sa.Integer, nullable=False),
        sa.Column("sanitized_text", sa.Text, nullable=False),
        sa.Column("char_count", sa.Integer, nullable=False),
        sa.Column("source_page", sa.Integer, nullable=True),
        sa.Column("source_slide", sa.Integer, nullable=True),
        sa.Column("source_sheet", sa.Integer, nullable=True),
        sa.Column("source_timestamp_start", sa.Float, nullable=True),
        sa.Column("source_timestamp_end", sa.Float, nullable=True),
        sa.Column("section_title", sa.String, nullable=True),
        sa.Column("chunk_type", sa.String, nullable=False, server_default="clause"),
        sa.Column("field_label", sa.String, nullable=True),
        sa.Column("field_value", sa.Text, nullable=True),
        sa.Column("field_type", sa.String, nullable=True),
    )

    # --- app_settings ---
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("llm_provider", sa.String, nullable=False),
        sa.Column("llm_model", sa.String, nullable=False),
        sa.Column("ollama_base_url", sa.String, nullable=False),
        sa.Column("ollama_chat_model", sa.String, nullable=False),
        sa.Column("ollama_deanon_model", sa.String, nullable=False),
        sa.Column("deanon_enabled", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("deanon_strategy", sa.String, nullable=False),
        sa.Column("require_approval", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("show_json_output", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("use_presidio_layer", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("use_ner_layer", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("use_ollama_validation_layer", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("use_ollama_layer", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("chunk_size", sa.Integer, nullable=False),
        sa.Column("chunk_overlap", sa.Integer, nullable=False),
        sa.Column("top_k_retrieval", sa.Integer, nullable=False),
        sa.Column("pdf_chunk_size", sa.Integer, nullable=False),
        sa.Column("audio_chunk_size", sa.Integer, nullable=False),
        sa.Column("spreadsheet_chunk_size", sa.Integer, nullable=False),
        sa.Column("whisper_model", sa.String, nullable=False),
        sa.Column("default_audio_language", sa.String, nullable=True),
        sa.Column("image_ocr_languages", sa.JSON, nullable=False),
        sa.Column("ocr_provider", sa.String, nullable=False),
        sa.Column("ocr_provider_options", sa.JSON, nullable=True),
        sa.Column("extract_embedded_images", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("recursive_email_attachments", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("default_active_regulations", sa.JSON, nullable=False),
        sa.Column("ner_model_overrides", sa.JSON, nullable=True),
    )

    # --- regulation_rulesets ---
    op.create_table(
        "regulation_rulesets",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("display_name", sa.String, nullable=False),
        sa.Column("region", sa.String, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("official_url", sa.String, nullable=True),
        sa.Column("entity_types", sa.JSON, nullable=False),
        sa.Column("is_builtin", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("custom_notes", sa.Text, nullable=True),
    )

    # --- custom_recognizers ---
    op.create_table(
        "custom_recognizers",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("entity_type", sa.String, nullable=False),
        sa.Column("detection_method", sa.String, nullable=False),
        sa.Column("pattern", sa.Text, nullable=True),
        sa.Column("keywords", sa.JSON, nullable=True),
        sa.Column("llm_prompt", sa.Text, nullable=True),
        sa.Column("context_words", sa.JSON, nullable=False),
        sa.Column("placeholder_label", sa.String, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )

    # --- non_pii_rules ---
    op.create_table(
        "non_pii_rules",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("pattern_type", sa.String, nullable=False),
        sa.Column("pattern", sa.Text, nullable=False),
        sa.Column("languages", sa.JSON, nullable=False),
        sa.Column("entity_types", sa.JSON, nullable=False),
        sa.Column("min_score", sa.Float, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )

    # --- errorlog ---
    op.create_table(
        "errorlog",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("source", sa.String(32), nullable=False, index=True),
        sa.Column("level", sa.String(16), nullable=False, index=True),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("exception_type", sa.String(256), nullable=True),
        sa.Column("stack_trace", sa.Text, nullable=True),
        sa.Column("path", sa.String(512), nullable=True, index=True),
        sa.Column("method", sa.String(16), nullable=True),
        sa.Column("status_code", sa.Integer, nullable=True),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("ip_hash", sa.String(128), nullable=True),
        sa.Column("extra", sa.JSON, nullable=True),
    )

    # --- spreadsheet_schemas ---
    op.create_table(
        "spreadsheet_schemas",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "document_id",
            sa.Integer,
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )

    # --- spreadsheet_columns ---
    op.create_table(
        "spreadsheet_columns",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "schema_id",
            sa.Integer,
            sa.ForeignKey("spreadsheet_schemas.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("index", sa.Integer, nullable=False),
        sa.Column("technical_label", sa.String, nullable=False),
        sa.Column("semantic_label", sa.String, nullable=True),
        sa.Column("is_numeric", sa.Boolean, nullable=True),
    )

    # --- text_normalization_rules ---
    op.create_table(
        "text_normalization_rules",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("pattern", sa.String, nullable=False),
        sa.Column("replacement", sa.String, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("text_normalization_rules")
    op.drop_table("spreadsheet_columns")
    op.drop_table("spreadsheet_schemas")
    op.drop_table("errorlog")
    op.drop_table("non_pii_rules")
    op.drop_table("custom_recognizers")
    op.drop_table("regulation_rulesets")
    op.drop_table("app_settings")
    op.drop_table("chunks")
    op.drop_table("documents")
