"""Replace audit_events' single-column document_id index with a composite one.

Creates ``ix_audit_events_document_event_id`` (document_id, event_type, id)
for the "latest event of one type per document" lookup (max(id) grouped by
document_id, filtered on event_type): the planner seeks the
(document_id, event_type) prefix and reads max(id) from the index, instead
of reading every audit event of each document and filtering on event_type.
Its leading column also serves every lookup the single-column
``ix_audit_events_document_id`` did (the per-document audit list and count,
the per-document trail and the erasure delete), so that index is dropped,
saving a write on every insert into this append-only table.

Both steps check the live schema first, so the upgrade is a no-op on a
database already in the target shape (built by ``create_all``). Downgrade
recreates the single-column index and drops the composite one. No data is
lost either way.

Revision ID: 021
Revises: 020
"""

import sqlalchemy as sa
from alembic import op

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None

_TABLE = "audit_events"
_COMPOSITE = "ix_audit_events_document_event_id"
_SINGLE = "ix_audit_events_document_id"


def _has_index(name: str) -> bool:
    return name in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(_TABLE)}


def upgrade() -> None:
    if not _has_index(_COMPOSITE):
        op.create_index(_COMPOSITE, _TABLE, ["document_id", "event_type", "id"])
    if _has_index(_SINGLE):
        op.drop_index(_SINGLE, table_name=_TABLE)


def downgrade() -> None:
    if not _has_index(_SINGLE):
        op.create_index(_SINGLE, _TABLE, ["document_id"])
    if _has_index(_COMPOSITE):
        op.drop_index(_COMPOSITE, table_name=_TABLE)
