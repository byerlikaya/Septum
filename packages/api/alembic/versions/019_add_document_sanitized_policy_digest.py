"""Add sanitized_policy_digest to documents.

A SHA-256 hex digest of what decided detection when the document was
sanitized, beyond its entity types: the detector version of the detection
library, the definitions of the active custom recognizers and non-PII
(allow-list) rules, and the detection settings (which layers run, NER model
overrides, and the Ollama model the validation and contextual layers call;
endpoint URLs are not included). The entity types are not part of it; they
are stored separately and compared as a set, so a policy with fewer types
never flags. The API compares the digest with the current one, so a new
detector version, an edited custom recognizer, a removed non-PII rule or a
changed detection layer marks the document stale. Nullable with no default
and no backfill: existing rows stay NULL, which readers treat as an unknown
(stale) policy.

The upgrade checks the live schema first, so it is a no-op on a database
whose ``documents`` table already has the column (built by ``create_all``).
Downgrade drops the column and the digests stored in it.

Revision ID: 019
Revises: 018
"""

import sqlalchemy as sa
from alembic import op

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if not _has_column("documents", "sanitized_policy_digest"):
        op.add_column(
            "documents",
            sa.Column("sanitized_policy_digest", sa.String(length=64), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_column("sanitized_policy_digest")
