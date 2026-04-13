"""Admin-scoped user management router. All routes require ``require_role('admin')``."""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.user import User
from ..services.auth import hash_password
from ..utils.auth_dependency import require_role
from ..utils.db_helpers import get_or_404
from .auth import require_strong_password

router = APIRouter(prefix="/api/users", tags=["users"])

UserRole = Literal["admin", "editor", "viewer"]


class UserItemResponse(BaseModel):
    """Serialized view of a user for list/detail endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    role: str
    is_active: bool
    created_at: datetime


class UserCreateRequest(BaseModel):
    """Payload for creating a new user from the admin panel."""

    email: EmailStr
    password: str
    role: UserRole = "editor"
    is_active: bool = True


class UserUpdateRequest(BaseModel):
    """Partial update payload for an existing user."""

    email: Optional[EmailStr] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None


class PasswordResetRequest(BaseModel):
    """Payload for an admin-initiated password reset."""

    new_password: str


async def _count_active_admins(db: AsyncSession, exclude_id: Optional[int] = None) -> int:
    """Return the number of active admins, optionally excluding one user."""
    stmt = select(func.count()).select_from(User).where(
        User.role == "admin",
        User.is_active.is_(True),
    )
    if exclude_id is not None:
        stmt = stmt.where(User.id != exclude_id)
    return (await db.scalar(stmt)) or 0


def _forbid_self(current_user: User, target_id: int, action: str) -> None:
    """Reject destructive self-targeted operations with a clear message."""
    if current_user.id == target_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You cannot {action} your own account",
        )


@router.get("", response_model=List[UserItemResponse])
async def list_users(
    _admin: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> List[UserItemResponse]:
    result = await db.execute(select(User).order_by(User.created_at.asc()))
    users = result.scalars().all()
    return [UserItemResponse.model_validate(u) for u in users]


@router.post("", response_model=UserItemResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreateRequest,
    _admin: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> UserItemResponse:
    require_strong_password(payload.password)

    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        role=payload.role,
        is_active=payload.is_active,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return UserItemResponse.model_validate(user)


@router.get("/{user_id}", response_model=UserItemResponse)
async def get_user(
    user_id: int,
    _admin: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> UserItemResponse:
    user = await get_or_404(db, User, user_id, "User not found")
    return UserItemResponse.model_validate(user)


@router.patch("/{user_id}", response_model=UserItemResponse)
async def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> UserItemResponse:
    target = await get_or_404(db, User, user_id, "User not found")
    updates = payload.model_dump(exclude_unset=True)

    role_changing = "role" in updates and updates["role"] != target.role
    deactivating = (
        "is_active" in updates
        and updates["is_active"] is False
        and target.is_active is True
    )

    if role_changing:
        _forbid_self(current_user, target.id, "change the role of")
    if deactivating:
        _forbid_self(current_user, target.id, "deactivate")

    if role_changing and target.role == "admin" and updates["role"] != "admin":
        if await _count_active_admins(db, exclude_id=target.id) == 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot demote the last active administrator",
            )

    if deactivating and target.role == "admin":
        if await _count_active_admins(db, exclude_id=target.id) == 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot deactivate the last active administrator",
            )

    if "email" in updates and updates["email"] != target.email:
        dupe = await db.execute(
            select(User).where(User.email == updates["email"], User.id != target.id)
        )
        if dupe.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already registered",
            )

    for field, value in updates.items():
        setattr(target, field, value)

    db.add(target)
    await db.commit()
    await db.refresh(target)
    return UserItemResponse.model_validate(target)


@router.post("/{user_id}/reset-password", response_model=UserItemResponse)
async def reset_password(
    user_id: int,
    payload: PasswordResetRequest,
    _admin: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> UserItemResponse:
    require_strong_password(payload.new_password)

    target = await get_or_404(db, User, user_id, "User not found")
    target.hashed_password = hash_password(payload.new_password)
    db.add(target)
    await db.commit()
    await db.refresh(target)
    return UserItemResponse.model_validate(target)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_user(
    user_id: int,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> None:
    target = await get_or_404(db, User, user_id, "User not found")
    _forbid_self(current_user, target.id, "delete")

    if target.role == "admin" and target.is_active:
        if await _count_active_admins(db, exclude_id=target.id) == 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot delete the last active administrator",
            )

    await db.delete(target)
    await db.commit()
