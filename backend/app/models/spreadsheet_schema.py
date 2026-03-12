from __future__ import annotations

"""ORM models for spreadsheet schema metadata.

These models describe the logical column layout for tabular documents
without storing any raw PII. Columns are identified by technical labels
such as ``COLUMN_1`` and can be optionally annotated with semantic labels
that describe their role in a generic, regulation-agnostic way.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SpreadsheetSchema(Base):
    """Represents the schema for a tabular document."""

    __tablename__ = "spreadsheet_schemas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utc_now, onupdate=_utc_now, nullable=False
    )

    columns: Mapped[list["SpreadsheetColumn"]] = relationship(
        back_populates="schema", cascade="all, delete-orphan", order_by="SpreadsheetColumn.index"
    )


class SpreadsheetColumn(Base):
    """Represents a single column within a spreadsheet schema."""

    __tablename__ = "spreadsheet_columns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    schema_id: Mapped[int] = mapped_column(
        ForeignKey("spreadsheet_schemas.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Zero-based column index within the tabular document.
    index: Mapped[int] = mapped_column(Integer, nullable=False)

    # Technical label (e.g., "COLUMN_1") derived from ingestion.
    technical_label: Mapped[str] = mapped_column(String, nullable=False)

    # Optional semantic label supplied by the user (e.g., "SALARY_MEASURE").
    semantic_label: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Optional flag indicating whether the column is predominantly numeric.
    is_numeric: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    schema: Mapped[SpreadsheetSchema] = relationship(back_populates="columns")

