from __future__ import annotations

"""Policy composer for Septum's sanitization pipeline.

Pure, database-free version of the composer. Accepts the three
backend model lists through Protocol types and returns a
:class:`ComposedPolicy` describing the effective entity set plus
Presidio recognizers.

Backend hosts that need to load the models from a database should
wrap this class with a thin adapter that calls
:meth:`PolicyComposer.compose_from_data` after fetching rows from
SQLAlchemy — this keeps the core package free of any DB coupling.
"""

from dataclasses import dataclass
from typing import List, Sequence

from presidio_analyzer import EntityRecognizer

from ..recognizers.registry import RecognizerRegistry
from .models import CustomRecognizerLike, NonPiiRuleLike, RegulationRulesetLike


@dataclass
class ComposedPolicy:
    """Represents the effective privacy policy for a sanitization run."""

    entity_types: List[str]
    recognizers: List[EntityRecognizer]
    regulation_ids: List[str]
    non_pii_rules: List[NonPiiRuleLike]


class PolicyComposer:
    """
    Compose the active policy from regulations and custom recognizers.

    The composer is intentionally decoupled from the database layer so that
    it can be exercised easily in unit tests. Framework code is expected to
    fetch active ``RegulationRulesetLike`` and ``CustomRecognizerLike``
    objects (for example via SQLAlchemy) and then pass them into
    :meth:`compose_from_data`.
    """

    def __init__(self, recognizer_registry: RecognizerRegistry | None = None) -> None:
        self._recognizer_registry = recognizer_registry or RecognizerRegistry()

    def compose_from_data(
        self,
        active_regs: Sequence[RegulationRulesetLike],
        active_custom: Sequence[CustomRecognizerLike],
        active_non_pii: Sequence[NonPiiRuleLike],
    ) -> ComposedPolicy:
        """
        Build a ``ComposedPolicy`` from in-memory models.

        - Entity types are the union of all types declared by active
          regulations plus the entity types of active custom recognizers.
        - Recognizers are provided by ``RecognizerRegistry``, which loads
          both regulation packs and custom recognizers.
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
            non_pii_rules=list(active_non_pii),
        )
