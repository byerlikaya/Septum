from __future__ import annotations

"""
Policy composer for Septum's sanitization pipeline.

This module defines `ComposedPolicy`, a lightweight data structure describing
the active entity types and Presidio recognizers, and `PolicyComposer`, which
assembles this structure from regulation rulesets and custom recognizers.
"""

from dataclasses import dataclass
from typing import List, Sequence

from presidio_analyzer import EntityRecognizer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .recognizers.registry import RecognizerRegistry
from ..models.regulation import CustomRecognizer, RegulationRuleset


@dataclass
class ComposedPolicy:
    """Represents the effective privacy policy for a sanitization run."""

    entity_types: List[str]
    recognizers: List[EntityRecognizer]
    regulation_ids: List[str]


class PolicyComposer:
    """
    Compose the active policy from regulations and custom recognizers.

    The composer is intentionally decoupled from the database layer so that
    it can be exercised easily in unit tests. Framework code is expected to
    fetch active `RegulationRuleset` and `CustomRecognizer` objects (for
    example via SQLAlchemy) and then pass them into `compose_from_data`.
    """

    def __init__(self, recognizer_registry: RecognizerRegistry | None = None) -> None:
        self._recognizer_registry = recognizer_registry or RecognizerRegistry()

    async def compose(self, db: AsyncSession) -> ComposedPolicy:
        """
        Load active regulations and custom recognizers from the database and
        return the composed policy.
        """
        regs_result = await db.execute(
            select(RegulationRuleset).where(RegulationRuleset.is_active.is_(True))
        )
        active_regs = list(regs_result.scalars().all())

        custom_result = await db.execute(
            select(CustomRecognizer).where(CustomRecognizer.is_active.is_(True))
        )
        active_custom = list(custom_result.scalars().all())

        return self.compose_from_data(active_regs, active_custom)

    def compose_from_data(
        self,
        active_regs: Sequence[RegulationRuleset],
        active_custom: Sequence[CustomRecognizer],
    ) -> ComposedPolicy:
        """
        Build a `ComposedPolicy` from in-memory models.

        - Entity types are the union of all types declared by active
          regulations plus the entity types of active custom recognizers.
        - Recognizers are provided by `RecognizerRegistry`, which loads both
          regulation packs and custom recognizers.
        """
        entity_types_set: set[str] = set()
        regulation_ids: List[str] = []

        for reg in active_regs:
            regulation_ids.append(reg.id)
            for et in reg.entity_types or []:
                entity_types_set.add(et)

        for custom in active_custom:
            if custom.is_active:
                entity_types_set.add(custom.entity_type)

        recognizers = self._recognizer_registry.build(active_regs, active_custom)

        return ComposedPolicy(
            entity_types=sorted(entity_types_set),
            recognizers=recognizers,
            regulation_ids=regulation_ids,
        )

