"""Add use_ollama_semantic_layer to app_settings.

Backfill for the column that was added to the SQLAlchemy model in commit
5be3c45 ("Speed up chat by masking chunks from the existing anon map") with
only the SQLite ``_sqlite_ensure_columns`` ALTER TABLE — no Alembic
migration was created at the time, so PostgreSQL deployments hit
``UndefinedColumnError`` on first ``SELECT`` from ``app_settings`` after the
setup wizard runs ``alembic upgrade head``.

Defaults to ``false`` so the semantic-layer Ollama PII detection step stays
opt-in (the same default the model uses).

Revision ID: 011
"""

from alembic import op
import sqlalchemy as sa

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column(
            "use_ollama_semantic_layer",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("app_settings", "use_ollama_semantic_layer")
