"""FastAPI dependency for extracting the current authenticated user from JWT."""

from __future__ import annotations

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.settings import AppSettings
from ..models.user import User
from ..services.auth import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


async def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Decode the Bearer token and return the corresponding ``User``.

    Raises 401 if the token is missing, expired, or invalid.
    """
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )
    return user


async def get_optional_user(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Return the current user if a valid token is present, otherwise ``None``.

    Used for endpoints that work both authenticated and anonymously
    (backward compatibility during migration).
    """
    if token is None:
        return None
    try:
        return await get_current_user(token, db)
    except HTTPException:
        return None


def require_role(*allowed_roles: str):
    """Return a dependency that enforces one or more roles.

    Usage::

        @router.patch("/settings", dependencies=[Depends(require_role("admin"))])
        async def update_settings(...): ...
    """

    async def _check(
        current_user: User = Depends(get_current_user),
    ) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return _check


async def _is_bootstrap_mode(db: AsyncSession) -> bool:
    """Return True while the system is still in first-run bootstrap.

    Bootstrap mode is defined as: the ``users`` table is empty **and**
    ``AppSettings.setup_completed`` is ``False``. This is the window
    the setup wizard runs in — no admin user exists yet, so admin-only
    endpoints cannot be protected by a JWT. Once the first admin is
    created or the wizard flips ``setup_completed`` to ``True``, this
    returns ``False`` and admin enforcement resumes.
    """
    user_count_result = await db.execute(select(func.count()).select_from(User))
    user_count = int(user_count_result.scalar_one())
    if user_count > 0:
        return False

    settings_result = await db.execute(
        select(AppSettings.setup_completed).where(AppSettings.id == 1)
    )
    completed = settings_result.scalar_one_or_none()
    return not bool(completed)


async def require_admin_or_bootstrap(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Admin-only under normal operation, anonymous during bootstrap.

    The setup wizard needs to configure LLM providers, activate
    regulations and patch app settings before the first admin exists.
    Those endpoints used to be open when the role column on
    ``users`` was cosmetic; after the RBAC commit they are all gated
    by ``require_role("admin")``, which breaks the wizard because no
    admin is present yet.

    This dependency relaxes admin enforcement **only** while the
    system is still in first-run bootstrap state (see
    :func:`_is_bootstrap_mode`). Once the first admin is created or
    ``setup_completed`` flips to ``True``, the dependency becomes
    strictly admin-only — equivalent to ``require_role("admin")``.
    Returns ``None`` during bootstrap so handlers that want to use
    the user must handle the optional case.
    """
    if await _is_bootstrap_mode(db):
        return None

    user = await get_current_user(token, db)
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions",
        )
    return user


async def require_user_or_bootstrap(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Authenticated under normal operation, anonymous during bootstrap.

    Same bootstrap escape hatch as :func:`require_admin_or_bootstrap`
    but for endpoints that only require *some* authenticated user
    (admin / editor / viewer) outside of first-run setup. The setup
    wizard's ``GET /api/regulations`` call is the canonical consumer:
    after setup completes, any role may list regulations, but during
    first-run the wizard has no token to present.
    """
    if await _is_bootstrap_mode(db):
        return None
    return await get_current_user(token, db)
