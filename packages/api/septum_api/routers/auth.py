"""FastAPI router for user authentication (register, login, me, change-password)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import exists, insert, literal, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import bootstrap
from ..database import get_db
from ..middleware.rate_limit import (
    check_login_failures,
    get_rate_limit_key,
    record_login_failure,
    route_limit,
)
from ..models.user import User
from ..services.auth import (
    WeakPasswordError,
    create_access_token,
    hash_password,
    run_password_work,
    validate_password_strength,
    verify_password,
    verify_password_or_dummy,
)
from ..utils.auth_dependency import (
    deny_setup_access,
    get_current_user,
    require_interactive_session,
    require_setup_window,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_first_admin_lock = asyncio.Lock()

# All IPv6 clients of one /48 together: twelve clients' worth of the 5/min
# login limit, far above what one site's real sign-ins need, while 65 536
# rotating /64s in that /48 would otherwise get 5/min each.
_IPV6_SITE_AUTH_LIMIT = "60/minute"


def require_strong_password(password: str) -> None:
    """Wrap :func:`validate_password_strength` as an HTTP 400 for routers."""
    try:
        validate_password_strength(password)
    except WeakPasswordError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


class RegisterRequest(BaseModel):
    """Payload for creating a new user account."""

    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    """Payload for authenticating an existing user."""

    email: EmailStr
    password: str


class ChangePasswordRequest(BaseModel):
    """Payload for the authenticated user to change their own password."""

    current_password: str = Field(..., min_length=1)
    new_password: str


class TokenResponse(BaseModel):
    """JWT access token envelope."""

    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    """Public user profile."""

    id: int
    email: str
    role: str
    is_active: bool


async def _insert_first_admin(db: AsyncSession, email: str, hashed_password: str) -> bool:
    """Insert the first admin only if the users table is empty; return whether it was inserted.

    Check and insert are one ``INSERT ... SELECT ... WHERE NOT EXISTS``
    statement. SQLite runs it under its single-writer lock, so it is atomic
    there. On PostgreSQL two such statements could both see an empty table,
    so the insert runs in a fresh transaction whose first statement is
    ``LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE``. That mode conflicts
    with itself and with inserts, and ``LOCK`` takes no snapshot, so the
    insert's snapshot is taken after the lock is held and sees any admin a
    concurrent call committed, under READ COMMITTED and REPEATABLE READ
    alike. The caller's earlier reads are committed first so they cannot
    pin an older snapshot; the lock is released by the commit or rollback
    that ends the transaction.
    """
    if db.get_bind().dialect.name == "postgresql":
        await db.commit()
        await db.execute(text("LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE"))
    result = await db.execute(
        insert(User).from_select(
            ["email", "hashed_password", "role"],
            select(
                literal(email), literal(hashed_password), literal("admin")
            ).where(~exists(select(User.id))),
        )
    )
    return result.rowcount == 1


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@route_limit("3/minute", fail_open=False, ipv6_site_limit=_IPV6_SITE_AUTH_LIMIT)
async def register(
    request: Request,
    payload: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Create the very first user (admin) during setup, with the setup token.

    Concurrent calls are serialised in-process by a lock and across
    processes by :func:`_insert_first_admin`, so exactly one succeeds; the
    others get the generic setup 403. The stored setup token is deleted
    right after the commit. If that deletion fails the error propagates;
    the token is already useless because setup is closed, and the next
    start removes it. The route limit is counted when the request starts,
    so rejected attempts count too.
    """
    await require_setup_window(request, db)
    require_strong_password(payload.password)
    hashed = await run_password_work(hash_password, payload.password, lane="public")

    async with _first_admin_lock:
        created = await _insert_first_admin(db, payload.email, hashed)
        if not created:
            await db.rollback()
            deny_setup_access()
        await db.commit()
        bootstrap.clear_setup_token()

    user = (await db.execute(select(User).where(User.email == payload.email))).scalar_one()
    token = create_access_token(user.id, user.email)
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
@route_limit("5/minute", fail_open=False, ipv6_site_limit=_IPV6_SITE_AUTH_LIMIT)
async def login(
    request: Request,
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Authenticate a user and return a JWT.

    Limited per client (decorator) and by failed attempts per email
    (:func:`check_login_failures`); a successful login counts nothing.
    """
    await check_login_failures(request, payload.email)
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()

    stored_hash = user.hashed_password if user is not None else None
    if not await run_password_work(
        verify_password_or_dummy,
        payload.password,
        stored_hash,
        lane="public",
        identity=get_rate_limit_key(request),
    ):
        await record_login_failure(request, payload.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )

    token = create_access_token(user.id, user.email)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserResponse)
async def me(
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    """Return the profile of the currently authenticated user."""
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        role=current_user.role,
        is_active=current_user.is_active,
    )


@router.post(
    "/change-password",
    response_model=TokenResponse,
    dependencies=[Depends(require_interactive_session)],
)
@route_limit("5/minute", fail_open=False)
async def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Rotate the caller's own password and return a fresh JWT."""
    if not await run_password_work(
        verify_password,
        payload.current_password,
        current_user.hashed_password,
        lane="account",
        identity=f"user:{current_user.id}",
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    require_strong_password(payload.new_password)

    current_user.hashed_password = await run_password_work(
        hash_password, payload.new_password, lane="account", identity=f"user:{current_user.id}"
    )
    db.add(current_user)
    await db.commit()
    await db.refresh(current_user)

    token = create_access_token(current_user.id, current_user.email)
    return TokenResponse(access_token=token)
