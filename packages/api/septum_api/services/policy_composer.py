from __future__ import annotations

"""Backward-compatibility shim over :mod:`septum_core.regulations.composer`.

Keeps the original async ``PolicyComposer.compose(db)`` method on the
backend side — it loads active rulesets from the SQLAlchemy session
and then delegates the pure composition to septum-core.
"""

from septum_core.regulations.composer import ComposedPolicy
from septum_core.regulations.composer import PolicyComposer as _CorePolicyComposer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.regulation import CustomRecognizer, NonPiiRule, RegulationRuleset
from ..models.settings import AppSettings
from .policy_snapshot import (
    PolicySnapshot,
    build_policy_snapshot,
    custom_recognizer_definition,
    detection_settings,
    has_llm_prompt_rule,
    non_pii_rule_definition,
)
from .recognizers.registry import RecognizerRegistry

__all__ = ["ComposedPolicy", "PolicyComposer"]


class PolicyComposer(_CorePolicyComposer):
    """Backend-side composer that can load active rulesets from a DB session."""

    def __init__(self, recognizer_registry: RecognizerRegistry | None = None) -> None:
        """Default to the backend registry so ``llm_prompt`` custom rules get their recognizer."""
        super().__init__(recognizer_registry or RecognizerRegistry())
        self._injected_registry = recognizer_registry

    async def compose(
        self, db: AsyncSession, settings: AppSettings | None = None
    ) -> ComposedPolicy:
        """
        Load active regulations and custom recognizers from the database and
        return the composed policy.

        ``settings`` supplies the Ollama URL and model ``llm_prompt`` rules
        call; production callers pass the row their sanitizer is built from.
        """
        active_regs, active_custom, active_non_pii = await self._load_active(db)
        return self._compose_for(settings, active_regs, active_custom, active_non_pii)

    async def compose_with_snapshot(
        self, db: AsyncSession, settings: AppSettings
    ) -> tuple[ComposedPolicy, PolicySnapshot]:
        """Compose the policy and its snapshot from one read of the active rows.

        The snapshot is what a document records about the policy it was
        sanitized under, so both come from the same rows, and its detection
        settings come from ``settings``, the row the caller's sanitizer is
        built from. The ``llm_prompt`` recognizers call the Ollama URL and
        model of the same row, so the model the digest records is the one used.
        """
        active_regs, active_custom, active_non_pii = await self._load_active(db)
        policy = self._compose_for(settings, active_regs, active_custom, active_non_pii)
        custom_definitions = [custom_recognizer_definition(c) for c in active_custom]
        snapshot = build_policy_snapshot(
            [r.entity_types for r in active_regs],
            custom_definitions,
            [non_pii_rule_definition(r) for r in active_non_pii],
            detection=detection_settings(
                settings, llm_prompt_active=has_llm_prompt_rule(custom_definitions)
            ),
        )
        return policy, snapshot

    def _compose_for(
        self,
        settings: AppSettings | None,
        active_regs: list[RegulationRuleset],
        active_custom: list[CustomRecognizer],
        active_non_pii: list[NonPiiRule],
    ) -> ComposedPolicy:
        """Compose with the injected registry, else one bound to ``settings``."""
        if settings is None or self._injected_registry is not None:
            return self.compose_from_data(active_regs, active_custom, active_non_pii)
        return _CorePolicyComposer(RecognizerRegistry(settings)).compose_from_data(
            active_regs, active_custom, active_non_pii
        )

    @staticmethod
    async def _load_active(
        db: AsyncSession,
    ) -> tuple[list[RegulationRuleset], list[CustomRecognizer], list[NonPiiRule]]:
        """Return the active regulations, custom recognizers and non-PII rules."""
        regs_result = await db.execute(
            select(RegulationRuleset).where(RegulationRuleset.is_active.is_(True))
        )
        custom_result = await db.execute(
            select(CustomRecognizer).where(CustomRecognizer.is_active.is_(True))
        )
        non_pii_result = await db.execute(
            select(NonPiiRule).where(NonPiiRule.is_active.is_(True))
        )
        return (
            list(regs_result.scalars().all()),
            list(custom_result.scalars().all()),
            list(non_pii_result.scalars().all()),
        )
