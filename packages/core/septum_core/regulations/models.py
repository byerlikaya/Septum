from __future__ import annotations

"""Structural Protocol types that duck-type the backend ORM models.

The core package cannot import SQLAlchemy models directly (they live in
the backend process and pull in the database layer). These Protocol
types describe the minimum attribute surface the composer and
recognizer registry need, so any object that exposes the same fields —
including the production SQLAlchemy models — can be passed in without
runtime adaptation.
"""

from typing import List, Optional, Protocol, runtime_checkable


@runtime_checkable
class RegulationRulesetLike(Protocol):
    """Read-only view of a regulation ruleset row.

    ``entity_types`` is the union of PII categories the regulation
    requires to be masked; the composer uses it to assemble
    :attr:`ComposedPolicy.entity_types`.
    """

    id: str
    entity_types: List[str]


@runtime_checkable
class CustomRecognizerLike(Protocol):
    """Read-only view of a user-defined custom recognizer row.

    The fields mirror the backend's ``CustomRecognizer`` ORM model so
    that the core registry can build a Presidio recognizer without
    importing any backend code.
    """

    id: int
    name: str
    entity_type: str
    detection_method: str
    pattern: Optional[str]
    keywords: Optional[List[str]]
    llm_prompt: Optional[str]
    context_words: List[str]
    placeholder_label: str
    is_active: bool


@runtime_checkable
class NonPiiRuleLike(Protocol):
    """Read-only view of a non-PII rule row used by the non-PII filter."""

    pattern_type: str
    pattern: str
    languages: List[str]
    entity_types: List[str]
    min_score: Optional[float]
    is_active: bool
