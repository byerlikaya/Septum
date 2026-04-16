"""Make timestamp columns timezone-aware on PostgreSQL.

Several models declared their ``created_at`` / ``updated_at`` /
``uploaded_at`` columns as bare ``DateTime`` (mapped to PostgreSQL
``TIMESTAMP WITHOUT TIME ZONE``) but used a tz-aware default
(``datetime.now(timezone.utc)``). asyncpg refuses to insert a tz-aware
``datetime`` into a tz-naive column, so the very first INSERT against any
of these tables crashed with::

    asyncpg.exceptions.DataError: invalid input for query argument $N:
    can't subtract offset-naive and offset-aware datetimes

The seed step in :func:`app.database._seed_defaults` always inserts a
non-PII rule on first startup, so the bug crashed the application before
the user could even reach the chat. SQLite is more forgiving and never
hit this in development.

This migration converts the affected columns to ``TIMESTAMP WITH TIME
ZONE``. Existing values are interpreted as UTC (which matches what every
caller meant to write).

Revision ID: 012
"""

from alembic import op
import sqlalchemy as sa

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


# (table, column) pairs that need to flip from naive to aware.
_AWARE_COLUMNS: list[tuple[str, str]] = [
    ("documents", "uploaded_at"),
    ("users", "created_at"),
    ("chat_sessions", "created_at"),
    ("chat_sessions", "updated_at"),
    ("chat_messages", "created_at"),
    ("custom_recognizers", "created_at"),
    ("non_pii_rules", "created_at"),
    ("spreadsheet_schemas", "created_at"),
    ("spreadsheet_schemas", "updated_at"),
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite stores timestamps as text/integer and does not enforce
        # the TIMESTAMP WITH TIME ZONE distinction, so this migration is
        # a PostgreSQL-only fix. Skip silently on other backends.
        return
    for table, column in _AWARE_COLUMNS:
        op.alter_column(
            table,
            column,
            type_=sa.DateTime(timezone=True),
            existing_type=sa.DateTime(),
            existing_nullable=False,
            postgresql_using=f"{column} AT TIME ZONE 'UTC'",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table, column in reversed(_AWARE_COLUMNS):
        op.alter_column(
            table,
            column,
            type_=sa.DateTime(),
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
            postgresql_using=f"{column} AT TIME ZONE 'UTC'",
        )
