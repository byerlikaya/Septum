"""Add audit_event_id FK to entity_detections.

Every EntityDetection row now carries the audit event that produced it,
so the dashboard can jump from an audit log entry straight to the exact
entities highlighted in the document preview. Nullable — rows from
older ingestion runs have NULL and are still shown via the general
"View entities" button, just without event focus.

Revision ID: 015
"""

from alembic import op
import sqlalchemy as sa

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "entity_detections",
        sa.Column(
            "audit_event_id",
            sa.Integer(),
            sa.ForeignKey("audit_events.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_entity_detections_audit_event_id",
        "entity_detections",
        ["audit_event_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_entity_detections_audit_event_id", "entity_detections")
    op.drop_column("entity_detections", "audit_event_id")
