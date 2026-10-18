"""Reconcile the migrated schema with the ORM models.

Migrations 001-015 never created ``api_keys``, ``entity_index``,
``document_relationships`` or ``chunks.raw_text``; SQLite installs got them
from ``create_all`` while PostgreSQL installs did not. This revision adds
them and replaces the non-unique ``ix_users_email`` index plus the separate
unique constraint with the single unique index the model declares.

Every step checks the live schema first, so the revision is a no-op on a
database whose tables were already built by ``create_all``.

PostgreSQL installs migrated before 005/015 were rewritten keep their
auto-generated FK names (``*_fkey``); fresh installs get ``fk_*`` names.
Downgrade drops ``api_keys``, ``entity_index``, ``document_relationships``
and ``chunks.raw_text`` unconditionally, losing their data.

Revision ID: 016
Revises: 015
"""

from alembic import op
import sqlalchemy as sa

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def _inspector() -> sa.engine.reflection.Inspector:
    return sa.inspect(op.get_bind())


def _create_index_if_missing(name: str, table: str, columns: list[str]) -> None:
    if name not in {ix["name"] for ix in _inspector().get_indexes(table)}:
        op.create_index(name, table, columns)


def upgrade() -> None:
    if not _inspector().has_table("api_keys"):
        op.create_table(
            "api_keys",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("prefix", sa.String(8), nullable=False),
            sa.Column("hashed_key", sa.String(), nullable=False),
            sa.Column("scopes", sa.Text(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        )
    _create_index_if_missing("ix_api_keys_user_id", "api_keys", ["user_id"])
    _create_index_if_missing("ix_api_keys_prefix", "api_keys", ["prefix"])

    if not _inspector().has_table("entity_index"):
        op.create_table(
            "entity_index",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "document_id",
                sa.Integer(),
                sa.ForeignKey("documents.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("entity_type", sa.String(64), nullable=False),
            sa.Column("value_hash", sa.String(64), nullable=False),
            sa.Column("occurrences", sa.Integer(), nullable=False),
            sa.UniqueConstraint(
                "document_id",
                "entity_type",
                "value_hash",
                name="uq_entity_index_doc_type_value",
            ),
        )
    _create_index_if_missing("ix_entity_index_document_id", "entity_index", ["document_id"])
    _create_index_if_missing("ix_entity_index_value_hash", "entity_index", ["value_hash"])
    _create_index_if_missing(
        "ix_entity_index_value_type", "entity_index", ["value_hash", "entity_type"]
    )

    if not _inspector().has_table("document_relationships"):
        op.create_table(
            "document_relationships",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "doc_a_id",
                sa.Integer(),
                sa.ForeignKey("documents.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "doc_b_id",
                sa.Integer(),
                sa.ForeignKey("documents.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("score", sa.Float(), nullable=False),
            sa.Column("shared_entity_count", sa.Integer(), nullable=False),
            sa.Column("shared_entity_types", sa.String(), nullable=False),
            sa.UniqueConstraint("doc_a_id", "doc_b_id", name="uq_doc_relationship_pair"),
        )
    _create_index_if_missing("ix_doc_relationship_score", "document_relationships", ["score"])
    _create_index_if_missing("ix_doc_relationship_doc_a", "document_relationships", ["doc_a_id"])
    _create_index_if_missing("ix_doc_relationship_doc_b", "document_relationships", ["doc_b_id"])

    if "raw_text" not in {col["name"] for col in _inspector().get_columns("chunks")}:
        with op.batch_alter_table("chunks") as batch:
            batch.add_column(sa.Column("raw_text", sa.Text(), nullable=True))

    _make_users_email_index_unique()


def _make_users_email_index_unique() -> None:
    """Swap the email index for a unique one without a window lacking uniqueness.

    The old unique constraint is dropped only after the unique index exists,
    inside the same transaction on PostgreSQL.
    """
    insp = _inspector()
    email_index = next(
        (ix for ix in insp.get_indexes("users") if ix["name"] == "ix_users_email"), None
    )
    if email_index is not None and email_index["unique"]:
        return
    email_constraints = [
        uc["name"]
        for uc in insp.get_unique_constraints("users")
        if uc["column_names"] == ["email"] and uc["name"]
    ]
    with op.batch_alter_table("users") as batch:
        if email_index is not None:
            batch.drop_index("ix_users_email")
        batch.create_index("ix_users_email", ["email"], unique=True)
        for name in email_constraints:
            batch.drop_constraint(name, type_="unique")


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_index("ix_users_email")
        batch.create_index("ix_users_email", ["email"], unique=False)
        batch.create_unique_constraint("users_email_key", ["email"])
    with op.batch_alter_table("chunks") as batch:
        batch.drop_column("raw_text")
    op.drop_table("document_relationships")
    op.drop_table("entity_index")
    op.drop_table("api_keys")
