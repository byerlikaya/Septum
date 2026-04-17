"""Add rag_relevance_threshold to app_settings.

Configurable relevance score threshold for the hybrid-retrieval filter.
Replaces the hardcoded ``RELEVANCE_SCORE_THRESHOLD = 0.4`` constant in
the chat router, allowing operators to tune how aggressively low-scoring
chunks are dropped before the approval modal / LLM prompt.

Revision ID: 014
"""

from alembic import op
import sqlalchemy as sa

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column(
            "rag_relevance_threshold",
            sa.Float(),
            nullable=False,
            server_default="0.35",
        ),
    )


def downgrade() -> None:
    op.drop_column("app_settings", "rag_relevance_threshold")
