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
from ..models.regulation import CustomRecognizer, NonPiiRule, RegulationRuleset
from ..models.user import User
from ..services.recognizers.registry import RecognizerRegistry
from ..utils.auth_dependency import get_current_user, require_role
from ..utils.db_helpers import validate_regex

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


class NonPiiRuleResponse(BaseModel):
    """Serialized view of a Non-PII rule."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    pattern_type: Literal["token", "regex"]
    pattern: str
    languages: List[str]
    entity_types: List[str]
    min_score: Optional[float] = None
    is_active: bool


class NonPiiRuleCreatePayload(BaseModel):
    """Request body for creating a new Non-PII rule."""

    pattern_type: Literal["token", "regex"]
    pattern: str
    languages: List[str] = Field(default_factory=list)
    entity_types: List[str] = Field(default_factory=list)
    min_score: Optional[float] = None
    is_active: bool = True


class NonPiiRuleUpdatePayload(BaseModel):
    """PATCH body for updating a Non-PII rule."""

    pattern_type: Optional[Literal["token", "regex"]] = None
    pattern: Optional[str] = None
    languages: Optional[List[str]] = None
    entity_types: Optional[List[str]] = None
    min_score: Optional[float] = None
    is_active: Optional[bool] = None


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
    _user: User = Depends(get_current_user),
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
    _user: User = Depends(require_role("admin")),
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
    _user: User = Depends(get_current_user),
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
    _user: User = Depends(get_current_user),
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
    _user: User = Depends(require_role("admin")),
) -> CustomRecognizerResponse:
    """Create a new custom recognizer definition.

    Regex patterns are validated before being persisted, as required by
    the project guidelines.
    """
    data = payload.model_dump()
    detection_method = data["detection_method"]

    if detection_method == "regex" and data.get("pattern"):
        error = validate_regex(data["pattern"] or "")
        if error is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid regex pattern: {error}",
            )

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
    _user: User = Depends(require_role("admin")),
) -> CustomRecognizerResponse:
    """Update an existing custom recognizer definition."""
    recognizer = await _get_custom_or_404(db, recognizer_id)
    update_data = payload.model_dump(exclude_unset=True)

    detection_method = update_data.get("detection_method", recognizer.detection_method)
    pattern = update_data.get("pattern", recognizer.pattern)
    if detection_method == "regex" and pattern:
        error = validate_regex(pattern)
        if error is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid regex pattern: {error}",
            )

    for field_name, value in update_data.items():
        setattr(recognizer, field_name, value)

    await db.commit()
    await db.refresh(recognizer)

    return CustomRecognizerResponse.model_validate(recognizer)


@router.delete(
    "/custom/{recognizer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_custom_recognizer(
    recognizer_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role("admin")),
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
    _user: User = Depends(require_role("admin")),
) -> CustomRecognizerTestResponse:
    """Test a custom recognizer against a sample text.

    The implementation reuses :class:`RecognizerRegistry` to build a
    Presidio recognizer for the given database record and runs it over
    the provided sample text. LLM-backed recognizers currently act as
    placeholders and therefore return no matches.
    """
    recognizer_model = await _get_custom_or_404(db, recognizer_id)

    registry = RecognizerRegistry()
    recognizers = registry._from_custom_recognizers(  # type: ignore[attr-defined]
        [recognizer_model]
    )
    if not recognizers:
        return CustomRecognizerTestResponse(matches=[])

    recognizer = recognizers[0]
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


@router.get(
    "/non-pii",
    response_model=List[NonPiiRuleResponse],
    status_code=status.HTTP_200_OK,
)
async def list_non_pii_rules(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> List[NonPiiRuleResponse]:
    """Return all configured Non-PII rules."""
    result = await db.execute(select(NonPiiRule).order_by(NonPiiRule.id))
    rules = list(result.scalars().all())
    return [NonPiiRuleResponse.model_validate(r) for r in rules]


@router.post(
    "/non-pii",
    response_model=NonPiiRuleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_non_pii_rule(
    payload: NonPiiRuleCreatePayload,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role("admin")),
) -> NonPiiRuleResponse:
    """Create a new Non-PII rule."""
    data = payload.model_dump()
    if data["pattern_type"] == "regex":
        error = validate_regex(data["pattern"])
        if error is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid regex pattern: {error}",
            )

    rule = NonPiiRule(
        pattern_type=data["pattern_type"],
        pattern=data["pattern"],
        languages=data.get("languages") or [],
        entity_types=data.get("entity_types") or [],
        min_score=data.get("min_score"),
        is_active=data.get("is_active", True),
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return NonPiiRuleResponse.model_validate(rule)


@router.patch(
    "/non-pii/{rule_id}",
    response_model=NonPiiRuleResponse,
    status_code=status.HTTP_200_OK,
)
async def update_non_pii_rule(
    rule_id: int,
    payload: NonPiiRuleUpdatePayload,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role("admin")),
) -> NonPiiRuleResponse:
    """Update an existing Non-PII rule."""
    result = await db.execute(select(NonPiiRule).where(NonPiiRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Non-PII rule not found.",
        )

    update_data = payload.model_dump(exclude_unset=True)
    pattern_type = update_data.get("pattern_type", rule.pattern_type)
    pattern = update_data.get("pattern", rule.pattern)
    if pattern_type == "regex" and pattern:
        error = validate_regex(pattern)
        if error is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid regex pattern: {error}",
            )

    for field_name, value in update_data.items():
        setattr(rule, field_name, value)

    await db.commit()
    await db.refresh(rule)
    return NonPiiRuleResponse.model_validate(rule)


@router.delete(
    "/non-pii/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_non_pii_rule(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role("admin")),
) -> None:
    """Delete a Non-PII rule."""
    result = await db.execute(select(NonPiiRule).where(NonPiiRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Non-PII rule not found.",
        )
    await db.delete(rule)
    await db.commit()

