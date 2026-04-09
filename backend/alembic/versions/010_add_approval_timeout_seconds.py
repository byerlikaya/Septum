"""Add approval_timeout_seconds to app_settings.

Persists the maximum number of seconds the chat approval gate will wait
for a user decision before auto-rejecting the session with
``timed_out=True``. Defaults to 300 (5 minutes).

Revision ID: 010
"""

from alembic import op
import sqlalchemy as sa

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column(
            "approval_timeout_seconds",
            sa.Integer(),
            nullable=False,
            server_default="300",
        ),
    )


def downgrade() -> None:
    op.drop_column("app_settings", "approval_timeout_seconds")
