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

__all__ = ["ComposedPolicy", "PolicyComposer"]


class PolicyComposer(_CorePolicyComposer):
    """Backend-side composer that can load active rulesets from a DB session."""

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

        non_pii_result = await db.execute(
            select(NonPiiRule).where(NonPiiRule.is_active.is_(True))
        )
        active_non_pii = list(non_pii_result.scalars().all())

        return self.compose_from_data(active_regs, active_custom, active_non_pii)
