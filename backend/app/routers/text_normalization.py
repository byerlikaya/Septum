from __future__ import annotations

"""Router for managing text normalization rules."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.text_normalization import TextNormalizationRule
from ..utils.db_helpers import validate_regex


router = APIRouter(prefix="/api/text-normalization", tags=["text-normalization"])


class TextNormalizationRulePayload(BaseModel):
    """Request/response schema for text normalization rules."""

    id: int | None = None
    name: str
    pattern: str
    replacement: str
    is_active: bool = True
    priority: int = 0


@router.get(
    "",
    response_model=List[TextNormalizationRulePayload],
    status_code=status.HTTP_200_OK,
)
async def list_rules(db: AsyncSession = Depends(get_db)) -> List[TextNormalizationRulePayload]:
    """Return all text normalization rules ordered by priority."""

    stmt = select(TextNormalizationRule).order_by(
        TextNormalizationRule.priority.asc(), TextNormalizationRule.id.asc()
    )
    result = await db.execute(stmt)
    rules = list(result.scalars().all())
    return [
        TextNormalizationRulePayload(
            id=r.id,
            name=r.name,
            pattern=r.pattern,
            replacement=r.replacement,
            is_active=r.is_active,
            priority=r.priority,
        )
        for r in rules
    ]


@router.post(
    "",
    response_model=TextNormalizationRulePayload,
    status_code=status.HTTP_201_CREATED,
)
async def create_rule(
    payload: TextNormalizationRulePayload,
    db: AsyncSession = Depends(get_db),
) -> TextNormalizationRulePayload:
    """Create a new text normalization rule."""

    error = validate_regex(payload.pattern)
    if error is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid regex pattern: {error}",
        )

    rule = TextNormalizationRule(
        name=payload.name,
        pattern=payload.pattern,
        replacement=payload.replacement,
        is_active=payload.is_active,
        priority=payload.priority,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return TextNormalizationRulePayload(
        id=rule.id,
        name=rule.name,
        pattern=rule.pattern,
        replacement=rule.replacement,
        is_active=rule.is_active,
        priority=rule.priority,
    )


@router.patch(
    "/{rule_id}",
    response_model=TextNormalizationRulePayload,
    status_code=status.HTTP_200_OK,
)
async def update_rule(
    rule_id: int,
    payload: TextNormalizationRulePayload,
    db: AsyncSession = Depends(get_db),
) -> TextNormalizationRulePayload:
    """Update an existing text normalization rule."""

    stmt = select(TextNormalizationRule).where(TextNormalizationRule.id == rule_id)
    result = await db.execute(stmt)
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Text normalization rule not found.",
        )

    error = validate_regex(payload.pattern)
    if error is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid regex pattern: {error}",
        )

    rule.name = payload.name
    rule.pattern = payload.pattern
    rule.replacement = payload.replacement
    rule.is_active = payload.is_active
    rule.priority = payload.priority

    await db.commit()
    await db.refresh(rule)
    return TextNormalizationRulePayload(
        id=rule.id,
        name=rule.name,
        pattern=rule.pattern,
        replacement=rule.replacement,
        is_active=rule.is_active,
        priority=rule.priority,
    )


@router.delete(
    "/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_rule(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a text normalization rule."""

    stmt = select(TextNormalizationRule).where(TextNormalizationRule.id == rule_id)
    result = await db.execute(stmt)
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Text normalization rule not found.",
        )

    await db.delete(rule)
    await db.commit()



