"""Database-driven text normalization service."""

from __future__ import annotations

import re
from typing import Callable, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.text_normalization import TextNormalizationRule


class TextNormalizer:
    """Applies configured text normalization rules to text."""

    async def normalize(self, db: AsyncSession, text: str) -> str:
        """Return ``text`` after applying all active normalization rules."""

        if not text:
            return text
        return (await self.compile(db))(text)

    async def compile(self, db: AsyncSession) -> Callable[[str], str]:
        """Load the active rules once; return a function applying them in priority order.

        Invalid patterns are skipped; they can be fixed via the settings UI.
        """
        compiled: List[tuple[re.Pattern[str], str]] = []
        for rule in await self._load_rules(db):
            try:
                compiled.append((re.compile(rule.pattern), rule.replacement))
            except re.error:
                continue

        def apply(text: str) -> str:
            for pattern, replacement in compiled:
                text = pattern.sub(replacement, text)
            return text

        return apply

    async def _load_rules(self, db: AsyncSession) -> List[TextNormalizationRule]:
        """Load all active normalization rules ordered by priority."""

        stmt = (
            select(TextNormalizationRule)
            .where(TextNormalizationRule.is_active.is_(True))
            .order_by(TextNormalizationRule.priority.asc(), TextNormalizationRule.id.asc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

