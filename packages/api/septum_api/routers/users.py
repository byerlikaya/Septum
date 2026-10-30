"""Admin-scoped user management router. All routes require ``require_role('admin')``."""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr, field_validator
from sqlalchemy import delete as sa_delete
from sqlalchemy import and_, exists, func, or_, select
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
_LAST_ADMIN_DELETE = "Cannot delete the last active administrator"

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

    @field_validator("email", "role", "is_active", mode="before")
    @classmethod
    def _refuse_null(cls, value: Any) -> Any:
        """Refuse an explicit ``null``: the columns are NOT NULL, and omitting the field keeps it."""
        if value is None:
            raise ValueError("must not be null")
        return value


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


async def _lock_admin_rows(db: AsyncSession, target_id: int) -> Optional[User]:
    """Lock the active admin rows and the target row, in id order, before an admin-losing write.

    On PostgreSQL two admin-losing writes each lock the other's target, an
    active admin until that write commits, so the second waits here and,
    under READ COMMITTED, re-checks the rows once released: the removed
    admin drops out of its count. Id order keeps two contenders from
    deadlocking. ``FOR NO KEY UPDATE`` is enough for that and, unlike
    ``FOR UPDATE``, does not hold back inserts whose foreign keys reference
    these rows. SQLite ignores the clause; there
    :func:`_require_an_active_admin` closes the race after the write.
    Returns the refreshed target, or ``None`` if it no longer exists.
    """
    locked = await db.scalars(
        select(User)
        .where(or_(and_(User.role == "admin", User.is_active.is_(True)), User.id == target_id))
        .order_by(User.id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    return next((user for user in locked if user.id == target_id), None)


async def _require_an_active_admin(db: AsyncSession, detail: str) -> None:
    """Flush the pending change, then roll back and raise 409 if no active admin is left.

    The count runs inside the writing transaction. On SQLite the flush took
    the single-writer lock, held until commit or rollback, so no other
    write can land between this count and the commit. On PostgreSQL the
    rows locked by :func:`_lock_admin_rows` keep the count valid until
    commit.
    """
    await db.flush()
    if await _count_active_admins(db) == 0:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


async def _raise_if_email_taken(
    db: AsyncSession, email: Optional[str], exclude_id: Optional[int] = None
) -> None:
    """Roll back a failed commit and raise 409 if ``email`` now belongs to another user.

    Called from an ``IntegrityError`` handler, which re-raises the original
    error when this returns: only the email uniqueness collision maps to
    409. Reading the table instead of parsing driver messages keeps it the
    same on SQLite and PostgreSQL.
    """
    await db.rollback()
    if email is None:
        return
    stmt = select(User.id).where(User.email == email)
    if exclude_id is not None:
        stmt = stmt.where(User.id != exclude_id)
    if await db.scalar(stmt) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_EMAIL_ALREADY_REGISTERED,
        ) from None


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
        # A concurrent create may have won the unique email after the check.
        await _raise_if_email_taken(db, payload.email)
        raise
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
    admin_loss = _admin_loss_on_update(updates)
    if admin_loss is not None:
        target = await _lock_admin_rows(db, user_id)
        if target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_USER_NOT_FOUND)

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

    await _refuse_taken_email(db, target, updates)

    for field, value in updates.items():
        setattr(target, field, value)

    db.add(target)
    try:
        if admin_loss is not None:
            await _require_an_active_admin(db, admin_loss)
        await db.commit()
    except IntegrityError:
        # A concurrent update or create may have taken the email after the check.
        await _raise_if_email_taken(db, updates.get("email"), exclude_id=user_id)
        raise
    await db.refresh(target)
    return UserItemResponse.model_validate(target)


def _admin_loss_on_update(updates: dict) -> Optional[str]:
    """Return the 409 detail if the update could remove an active admin, else ``None``.

    Decided from the payload alone, never from a read of the target: a
    target read as an editor may be the only admin by the time the write
    lands, so every demotion or deactivation is locked and recounted.
    """
    if "role" in updates and updates["role"] != "admin":
        return "Cannot demote the last active administrator"
    if updates.get("is_active") is False:
        return "Cannot deactivate the last active administrator"
    return None


async def _refuse_taken_email(db: AsyncSession, target: User, updates: dict) -> None:
    """Raise 409 when a changed email already belongs to another user."""
    if "email" not in updates or updates["email"] == target.email:
        return
    dupe = await db.execute(
        select(User).where(User.email == updates["email"], User.id != target.id)
    )
    if dupe.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_EMAIL_ALREADY_REGISTERED,
        )


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
    before anything is changed. The user is then deactivated in its own
    committed transaction under the last-admin guard, so the last active
    admin is refused before any document is erased. Each document is then
    erased exactly like ``DELETE /api/documents/{id}`` and committed on its
    own, so a failed erasure returns 500 with the user, now inactive, and
    the remaining documents in place; a retry finishes the job. The user
    row goes last, in one statement that deletes it only while it is still
    inactive and no document is left: a reactivation or an upload meanwhile
    refuses with 409 and leaves the user inactive. Chat sessions and API
    keys go with the row through their ``ON DELETE CASCADE`` foreign keys.
    """
    target = await get_or_404(db, User, user_id, _USER_NOT_FOUND)
    _forbid_self(current_user, target.id, "delete")

    documents = (
        await db.scalars(
            select(Document).where(Document.user_id == user_id).order_by(Document.id)
        )
    ).all()
    for document in documents:
        refuse_while_processing(document)
    await _deactivate_for_deletion(db, user_id)
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

    # One statement checks and deletes, so no engine can slip a document or
    # a reactivation in between: SQLite runs it under its write lock, and on
    # Postgres the row lock above already holds back uploads and updates.
    deleted = await db.execute(
        sa_delete(User)
        .where(
            User.id == user_id,
            User.is_active.is_(False),
            ~exists().where(Document.user_id == user_id),
        )
        .execution_options(synchronize_session=False)
    )
    if deleted.rowcount == 0:
        await _refuse_failed_user_delete(db, user_id)
    await forget_user_in_audit_events(db, user_id)
    await db.commit()


async def _deactivate_for_deletion(db: AsyncSession, user_id: int) -> None:
    """Deactivate the user and commit, refusing with 409 if no active admin would remain.

    This is the step of a delete that can remove an admin, so it carries
    the guard; a user already inactive is left unchanged.
    """
    target = await _lock_admin_rows(db, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_USER_NOT_FOUND)
    if target.is_active:
        target.is_active = False
        await _require_an_active_admin(db, _LAST_ADMIN_DELETE)
    await db.commit()


async def _refuse_failed_user_delete(db: AsyncSession, user_id: int) -> None:
    """Raise the 404 or 409 that explains why the final user delete matched no row."""
    is_active = await db.scalar(select(User.is_active).where(User.id == user_id))
    if is_active is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_USER_NOT_FOUND)
    if is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The account was reactivated during the deletion. Try again.",
        )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="A document was added to this account during the deletion. Try again.",
    )
