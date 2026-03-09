from __future__ import annotations

"""FastAPI router for regulation rulesets and custom recognizers.

This router exposes:

* Listing and toggling built-in and user-defined regulation rulesets.
* CRUD operations for user-defined :class:`CustomRecognizer` definitions.
* A simple test endpoint for validating recognizer behavior against sample text.
"""

from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.regulation import CustomRecognizer, RegulationRuleset
from ..services.recognizers.registry import RecognizerRegistry


router = APIRouter(prefix="/api/regulations", tags=["regulations"])


class RegulationRulesetResponse(BaseModel):
    """Serialized view of a regulation ruleset."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    display_name: str
    region: str
    description: Optional[str] = None
    official_url: Optional[str] = None
    entity_types: List[str]
    is_builtin: bool
    is_active: bool
    custom_notes: Optional[str] = None


class RegulationActivatePayload(BaseModel):
    """PATCH payload for toggling a regulation ruleset."""

    is_active: bool = Field(..., description="Whether the ruleset should be active.")


class CustomRecognizerResponse(BaseModel):
    """Serialized view of a user-defined custom recognizer."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    entity_type: str
    detection_method: str
    pattern: Optional[str] = None
    keywords: Optional[List[str]] = None
    llm_prompt: Optional[str] = None
    context_words: List[str]
    placeholder_label: str
    is_active: bool


DetectionMethod = Literal["regex", "keyword_list", "llm_prompt"]


class CustomRecognizerCreatePayload(BaseModel):
    """Request body for creating a new custom recognizer."""

    name: str
    entity_type: str
    detection_method: DetectionMethod
    pattern: Optional[str] = None
    keywords: Optional[List[str]] = None
    llm_prompt: Optional[str] = None
    context_words: List[str] = Field(default_factory=list)
    placeholder_label: str
    is_active: bool = True


class CustomRecognizerUpdatePayload(BaseModel):
    """PATCH body for updating a custom recognizer."""

    name: Optional[str] = None
    entity_type: Optional[str] = None
    detection_method: Optional[DetectionMethod] = None
    pattern: Optional[str] = None
    keywords: Optional[List[str]] = None
    llm_prompt: Optional[str] = None
    context_words: Optional[List[str]] = None
    placeholder_label: Optional[str] = None
    is_active: Optional[bool] = None


class CustomRecognizerTestRequest(BaseModel):
    """Request body for testing a custom recognizer against sample text."""

    sample_text: str


class CustomRecognizerTestMatch(BaseModel):
    """Single match result from a custom recognizer test."""

    text: str
    start: int
    end: int
    score: float


class CustomRecognizerTestResponse(BaseModel):
    """Response describing all matches from a custom recognizer test."""

    matches: List[CustomRecognizerTestMatch]


async def _get_ruleset_or_404(
    db: AsyncSession,
    ruleset_id: str,
) -> RegulationRuleset:
    result = await db.execute(
        select(RegulationRuleset).where(RegulationRuleset.id == ruleset_id)
    )
    ruleset = result.scalar_one_or_none()
    if ruleset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Regulation ruleset not found.",
        )
    return ruleset


async def _get_custom_or_404(
    db: AsyncSession,
    recognizer_id: int,
) -> CustomRecognizer:
    result = await db.execute(
        select(CustomRecognizer).where(CustomRecognizer.id == recognizer_id)
    )
    recognizer = result.scalar_one_or_none()
    if recognizer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Custom recognizer not found.",
        )
    return recognizer


@router.get(
    "",
    response_model=List[RegulationRulesetResponse],
    status_code=status.HTTP_200_OK,
)
async def list_regulation_rulesets(
    db: AsyncSession = Depends(get_db),
) -> List[RegulationRulesetResponse]:
    """Return all regulation rulesets, both built-in and custom."""
    result = await db.execute(select(RegulationRuleset).order_by(RegulationRuleset.id))
    rulesets = list(result.scalars().all())
    return [RegulationRulesetResponse.model_validate(r) for r in rulesets]


@router.patch(
    "/{ruleset_id}/activate",
    response_model=RegulationRulesetResponse,
    status_code=status.HTTP_200_OK,
)
async def activate_regulation_ruleset(
    ruleset_id: str,
    payload: RegulationActivatePayload,
    db: AsyncSession = Depends(get_db),
) -> RegulationRulesetResponse:
    """Activate or deactivate a specific regulation ruleset."""
    ruleset = await _get_ruleset_or_404(db, ruleset_id)
    ruleset.is_active = payload.is_active
    await db.commit()
    await db.refresh(ruleset)
    return RegulationRulesetResponse.model_validate(ruleset)


@router.get(
    "/{ruleset_id}/entities",
    response_model=List[str],
    status_code=status.HTTP_200_OK,
)
async def get_regulation_entities(
    ruleset_id: str,
    db: AsyncSession = Depends(get_db),
) -> List[str]:
    """Return the entity types covered by a specific regulation ruleset."""
    ruleset = await _get_ruleset_or_404(db, ruleset_id)
    return list(ruleset.entity_types or [])


@router.get(
    "/custom",
    response_model=List[CustomRecognizerResponse],
    status_code=status.HTTP_200_OK,
)
async def list_custom_recognizers(
    db: AsyncSession = Depends(get_db),
) -> List[CustomRecognizerResponse]:
    """Return all user-defined custom recognizers."""
    result = await db.execute(select(CustomRecognizer).order_by(CustomRecognizer.id))
    recognizers = list(result.scalars().all())
    return [CustomRecognizerResponse.model_validate(cr) for cr in recognizers]


@router.post(
    "/custom",
    response_model=CustomRecognizerResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_custom_recognizer(
    payload: CustomRecognizerCreatePayload,
    db: AsyncSession = Depends(get_db),
) -> CustomRecognizerResponse:
    """Create a new custom recognizer definition.

    Regex patterns are validated before being persisted, as required by
    the project guidelines.
    """
    data = payload.model_dump()
    detection_method = data["detection_method"]

    # Validate regex syntax eagerly.
    if detection_method == "regex" and data.get("pattern"):
        import re

        try:
            re.compile(data["pattern"] or "")
        except re.error as exc:  # noqa: PERF203
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid regex pattern: {exc}",
            ) from exc

    recognizer = CustomRecognizer(
        name=data["name"],
        entity_type=data["entity_type"],
        detection_method=detection_method,
        pattern=data.get("pattern"),
        keywords=data.get("keywords"),
        llm_prompt=data.get("llm_prompt"),
        context_words=data.get("context_words") or [],
        placeholder_label=data["placeholder_label"],
        is_active=data.get("is_active", True),
    )
    db.add(recognizer)
    await db.commit()
    await db.refresh(recognizer)

    return CustomRecognizerResponse.model_validate(recognizer)


@router.patch(
    "/custom/{recognizer_id}",
    response_model=CustomRecognizerResponse,
    status_code=status.HTTP_200_OK,
)
async def update_custom_recognizer(
    recognizer_id: int,
    payload: CustomRecognizerUpdatePayload,
    db: AsyncSession = Depends(get_db),
) -> CustomRecognizerResponse:
    """Update an existing custom recognizer definition."""
    recognizer = await _get_custom_or_404(db, recognizer_id)
    update_data = payload.model_dump(exclude_unset=True)

    # If detection_method or pattern is changing and we end up with a regex
    # configuration, validate the pattern before saving.
    detection_method = update_data.get("detection_method", recognizer.detection_method)
    pattern = update_data.get("pattern", recognizer.pattern)
    if detection_method == "regex" and pattern:
        import re

        try:
            re.compile(pattern)
        except re.error as exc:  # noqa: PERF203
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid regex pattern: {exc}",
            ) from exc

    for field_name, value in update_data.items():
        setattr(recognizer, field_name, value)

    await db.commit()
    await db.refresh(recognizer)

    return CustomRecognizerResponse.model_validate(recognizer)


@router.delete(
    "/custom/{recognizer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_custom_recognizer(
    recognizer_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a custom recognizer definition."""
    recognizer = await _get_custom_or_404(db, recognizer_id)
    await db.delete(recognizer)
    await db.commit()


@router.post(
    "/custom/{recognizer_id}/test",
    response_model=CustomRecognizerTestResponse,
    status_code=status.HTTP_200_OK,
)
async def test_custom_recognizer(
    recognizer_id: int,
    payload: CustomRecognizerTestRequest,
    db: AsyncSession = Depends(get_db),
) -> CustomRecognizerTestResponse:
    """Test a custom recognizer against a sample text.

    The implementation reuses :class:`RecognizerRegistry` to build a
    Presidio recognizer for the given database record and runs it over
    the provided sample text. LLM-backed recognizers currently act as
    placeholders and therefore return no matches.
    """
    recognizer_model = await _get_custom_or_404(db, recognizer_id)

    registry = RecognizerRegistry()
    # Reuse the internal helper for building recognizers from CustomRecognizer
    # models to avoid duplicating mapping logic.
    recognizers = registry._from_custom_recognizers(  # type: ignore[attr-defined]
        [recognizer_model]
    )
    if not recognizers:
        return CustomRecognizerTestResponse(matches=[])

    recognizer = recognizers[0]
    # We intentionally use 'en' here; the recognizer itself may perform
    # language-agnostic matching (e.g. regex, keyword).
    results = recognizer.analyze(
        text=payload.sample_text,
        entities=[recognizer_model.entity_type],
        nlp_artifacts=None,
    )

    matches: List[CustomRecognizerTestMatch] = []
    for r in results:
        matches.append(
            CustomRecognizerTestMatch(
                text=payload.sample_text[r.start : r.end],
                start=r.start,
                end=r.end,
                score=float(r.score),
            )
        )

    return CustomRecognizerTestResponse(matches=matches)

