"""Add API key fields to app_settings.

Stores LLM API keys in the database so the setup wizard can configure
them through the setup wizard UI.

Revision ID: 009
"""

from alembic import op
import sqlalchemy as sa

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_settings", sa.Column("anthropic_api_key", sa.String(), nullable=True))
    op.add_column("app_settings", sa.Column("openai_api_key", sa.String(), nullable=True))
    op.add_column("app_settings", sa.Column("openrouter_api_key", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("app_settings", "openrouter_api_key")
    op.drop_column("app_settings", "openai_api_key")
    op.drop_column("app_settings", "anthropic_api_key")
