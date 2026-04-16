"""Add use_gateway to app_settings.

Backfill for the column that was added to the SQLAlchemy model in the
Phase 5 api-producer slice with only the SQLite ``_sqlite_ensure_columns``
ALTER TABLE — no Alembic migration was created at the time, so PostgreSQL
deployments hit ``UndefinedColumnError`` on the first ``SELECT`` against
``app_settings`` (e.g. the ``/api/setup/initialize`` flow).

Defaults to ``false`` so cloud LLM calls stay direct unless the operator
explicitly opts into gateway-mode (the same default the model + SQLite
auto-migrator + ``USE_GATEWAY_DEFAULT`` env fallback all use).

Revision ID: 013
"""

from alembic import op
import sqlalchemy as sa

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column(
            "use_gateway",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("app_settings", "use_gateway")
