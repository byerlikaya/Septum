"""Add entity_detections table for per-entity location tracking.

Revision ID: 007
Revises: 006
Create Date: 2026-04-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "entity_detections",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "document_id",
            sa.Integer,
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "chunk_id",
            sa.Integer,
            sa.ForeignKey("chunks.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("entity_type", sa.String(64), nullable=False, index=True),
        sa.Column("placeholder", sa.String(64), nullable=False),
        sa.Column("start_offset", sa.Integer, nullable=False),
        sa.Column("end_offset", sa.Integer, nullable=False),
        sa.Column("score", sa.Float, nullable=False, server_default="0.0"),
    )
    op.create_index(
        "ix_entity_detections_doc_chunk",
        "entity_detections",
        ["document_id", "chunk_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_entity_detections_doc_chunk", table_name="entity_detections")
    op.drop_table("entity_detections")
