"""Add users table and user_id FK to documents and chat_sessions.

Revision ID: 005
Revises: 004
Create Date: 2026-04-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"])

    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_documents_user_id_users", "users", ["user_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_documents_user_id", "documents", ["user_id"])

    with op.batch_alter_table("chat_sessions") as batch:
        batch.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_chat_sessions_user_id_users", "users", ["user_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_chat_sessions_user_id", "chat_sessions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_sessions_user_id", "chat_sessions")
    with op.batch_alter_table("chat_sessions") as batch:
        batch.drop_column("user_id")
    op.drop_index("ix_documents_user_id", "documents")
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("user_id")
    op.drop_index("ix_users_email", "users")
    op.drop_table("users")
