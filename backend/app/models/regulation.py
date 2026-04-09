from __future__ import annotations

"""ORM models related to privacy regulation rulesets and custom recognizers."""

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


class RegulationRuleset(Base):
    """Represents a built-in or custom privacy regulation ruleset.

    Uses a string primary key (e.g. regulation id like \"gdpr\") instead of an integer.
    """

    __tablename__ = "regulation_rulesets"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    region: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    official_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    entity_types: Mapped[List[str]] = mapped_column(JSON, nullable=False, default=list)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    custom_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class CustomRecognizer(Base):
    """User-defined custom recognizer configuration."""

    __tablename__ = "custom_recognizers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    detection_method: Mapped[str] = mapped_column(String, nullable=False)
    pattern: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    keywords: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True)
    llm_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    context_words: Mapped[List[str]] = mapped_column(JSON, nullable=False, default=list)
    placeholder_label: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class NonPiiRule(Base):
    """Configuration for spans that should be treated as non-PII.

    Rules are data-driven and language-agnostic. They can either match on a
    normalized token value (``pattern_type='token'``) or on a regular
    expression (``pattern_type='regex'``). Optional language and entity-type
    filters restrict where the rule applies.
    """

    __tablename__ = "non_pii_rules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    pattern_type: Mapped[str] = mapped_column(String, nullable=False)  # "token" | "regex"
    pattern: Mapped[str] = mapped_column(Text, nullable=False)

    languages: Mapped[List[str]] = mapped_column(JSON, nullable=False, default=list)
    entity_types: Mapped[List[str]] = mapped_column(JSON, nullable=False, default=list)

    min_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

