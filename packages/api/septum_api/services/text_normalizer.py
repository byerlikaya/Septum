"""Database-driven text normalization service."""

from __future__ import annotations

import re
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.text_normalization import TextNormalizationRule


class TextNormalizer:
    """Applies configured text normalization rules to text."""

    async def normalize(self, db: AsyncSession, text: str) -> str:
        """Return ``text`` after applying all active normalization rules."""

        if not text:
            return text

        rules = await self._load_rules(db)
        result = text

        for rule in rules:
            try:
                pattern = re.compile(rule.pattern)
            except re.error:
                # Invalid patterns are ignored; they can be fixed via the settings UI.
                continue
            result = pattern.sub(rule.replacement, result)

        return result

    async def _load_rules(self, db: AsyncSession) -> List[TextNormalizationRule]:
        """Load all active normalization rules ordered by priority."""

        stmt = (
            select(TextNormalizationRule)
            .where(TextNormalizationRule.is_active.is_(True))
            .order_by(TextNormalizationRule.priority.asc(), TextNormalizationRule.id.asc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

