"""FastAPI router for API key management (create, list, revoke)."""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.api_key import ApiKey
from ..models.user import User
from ..services.api_key_service import generate_api_key, list_user_keys, revoke_key
from ..utils.auth_dependency import get_current_user, require_role

router = APIRouter(prefix="/api/api-keys", tags=["api-keys"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class CreateApiKeyRequest(BaseModel):
    """Payload for creating a new API key."""

    name: str = Field(..., min_length=1, max_length=128)
    expires_at: datetime | None = None


class ApiKeyResponse(BaseModel):
    """Public API key metadata (never includes the raw key)."""

    id: int
    name: str
    prefix: str
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None


class ApiKeyCreatedResponse(ApiKeyResponse):
    """Returned once at creation — includes the raw key."""

    raw_key: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=ApiKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("admin"))],
)
async def create_api_key_endpoint(
    payload: CreateApiKeyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyCreatedResponse:
    """Create a new API key for the authenticated admin.

    The raw key is shown **once** in the response and never stored.
    """
    raw_key, api_key = await generate_api_key(
        db,
        user_id=current_user.id,
        name=payload.name,
        expires_at=payload.expires_at,
    )
    return ApiKeyCreatedResponse(
        id=api_key.id,
        name=api_key.name,
        prefix=api_key.prefix,
        is_active=api_key.is_active,
        created_at=api_key.created_at,
        last_used_at=api_key.last_used_at,
        expires_at=api_key.expires_at,
        raw_key=raw_key,
    )


@router.get("", response_model=list[ApiKeyResponse])
async def list_api_keys(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ApiKeyResponse]:
    """List all API keys owned by the authenticated user."""
    keys = await list_user_keys(db, current_user.id)
    return [
        ApiKeyResponse(
            id=k.id,
            name=k.name,
            prefix=k.prefix,
            is_active=k.is_active,
            created_at=k.created_at,
            last_used_at=k.last_used_at,
            expires_at=k.expires_at,
        )
        for k in keys
    ]


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revoke an API key. Admins can revoke any key; others only their own."""
    user_id = None if current_user.role == "admin" else current_user.id
    revoked = await revoke_key(db, key_id, user_id=user_id)
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )
