"""Admin-scoped user management router. All routes require ``require_role('admin')``."""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy import delete as sa_delete
from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.document import Document
from ..models.user import User
from ..services.audit_logger import forget_user_in_audit_events
from ..services.auth import hash_password, run_password_work
from ..utils.auth_dependency import require_interactive_session, require_role
from ..utils.db_helpers import get_or_404
from .auth import require_strong_password
from .documents import erase_document, refuse_while_processing

_EMAIL_ALREADY_REGISTERED = "Email already registered"
_USER_NOT_FOUND = "User not found"

router = APIRouter(
    prefix="/api/users",
    tags=["users"],
    dependencies=[Depends(require_interactive_session)],
)

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


@router.get("")
async def list_users(
    _admin: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> List[UserItemResponse]:
    result = await db.execute(select(User).order_by(User.created_at.asc()))
    users = result.scalars().all()
    return [UserItemResponse.model_validate(u) for u in users]


@router.post("", status_code=status.HTTP_201_CREATED)
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
            detail=_EMAIL_ALREADY_REGISTERED,
        )

    user = User(
        email=payload.email,
        hashed_password=await run_password_work(hash_password, payload.password),
        role=payload.role,
        is_active=payload.is_active,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        # A concurrent create won the unique email between the check and the insert.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_EMAIL_ALREADY_REGISTERED,
        ) from None
    await db.refresh(user)

    return UserItemResponse.model_validate(user)


@router.get("/{user_id}")
async def get_user(
    user_id: int,
    _admin: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> UserItemResponse:
    user = await get_or_404(db, User, user_id, _USER_NOT_FOUND)
    return UserItemResponse.model_validate(user)


@router.patch("/{user_id}")
async def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> UserItemResponse:
    target = await get_or_404(db, User, user_id, _USER_NOT_FOUND)
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
                detail=_EMAIL_ALREADY_REGISTERED,
            )

    for field, value in updates.items():
        setattr(target, field, value)

    db.add(target)
    await db.commit()
    await db.refresh(target)
    return UserItemResponse.model_validate(target)


@router.post("/{user_id}/reset-password")
async def reset_password(
    user_id: int,
    payload: PasswordResetRequest,
    _admin: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> UserItemResponse:
    require_strong_password(payload.new_password)

    target = await get_or_404(db, User, user_id, _USER_NOT_FOUND)
    target.hashed_password = await run_password_work(hash_password, payload.new_password)
    db.add(target)
    await db.commit()
    await db.refresh(target)
    return UserItemResponse.model_validate(target)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_user(
    user_id: int,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a user after erasing every stored copy of each of their documents.

    A document still being ingested refuses the whole delete with 409
    before anything is erased. Each document is then erased exactly like
    ``DELETE /api/documents/{id}`` and committed on its own, so a failed
    erasure returns 500 with the user and the remaining documents in place;
    a retry finishes the job. The user row goes last, in one statement that
    deletes it only while no document is left: one uploaded meanwhile
    refuses with 409 instead of losing its row to the cascade with its files
    left behind. Chat sessions and API keys go with the row through their
    ``ON DELETE CASCADE`` foreign keys.
    """
    target = await get_or_404(db, User, user_id, _USER_NOT_FOUND)
    _forbid_self(current_user, target.id, "delete")
    await _refuse_last_active_admin(db, target)

    documents = (
        await db.scalars(
            select(Document).where(Document.user_id == user_id).order_by(Document.id)
        )
    ).all()
    for document in documents:
        refuse_while_processing(document)
    for document in documents:
        await erase_document(db, document)

    target = await db.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_USER_NOT_FOUND)
    await _refuse_last_active_admin(db, target)

    # One statement checks and deletes, so no engine can slip a document in
    # between: SQLite runs it under its write lock, and on Postgres the row
    # lock above already holds back uploads that reference this user.
    deleted = await db.execute(
        sa_delete(User)
        .where(
            User.id == user_id,
            ~exists().where(Document.user_id == user_id),
        )
        .execution_options(synchronize_session=False)
    )
    if deleted.rowcount == 0:
        if await db.scalar(select(User.id).where(User.id == user_id)) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_USER_NOT_FOUND)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A document was added to this account during the deletion. Try again.",
        )
    await forget_user_in_audit_events(db, user_id)
    await db.commit()


async def _refuse_last_active_admin(db: AsyncSession, target: User) -> None:
    """Raise 409 if ``target`` is the last active administrator."""
    if target.role == "admin" and target.is_active:
        if await _count_active_admins(db, exclude_id=target.id) == 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot delete the last active administrator",
            )
