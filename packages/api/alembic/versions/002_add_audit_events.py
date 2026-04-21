"""Add audit_events table for GDPR/KVKK compliance tracking.

Revision ID: 002
Revises: 001
Create Date: 2026-04-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("event_type", sa.String(64), nullable=False, index=True),
        sa.Column("session_id", sa.String(128), nullable=True, index=True),
        sa.Column("document_id", sa.Integer, nullable=True, index=True),
        sa.Column("regulation_ids", sa.JSON, nullable=False),
        sa.Column("entity_types_detected", sa.JSON, nullable=False),
        sa.Column("entity_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("extra", sa.JSON, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("audit_events")
