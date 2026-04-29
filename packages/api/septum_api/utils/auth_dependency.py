"""FastAPI dependency for extracting the current authenticated user.

When ``AuthMiddleware`` is active it pre-resolves JWT / API key
credentials and attaches the ``User`` to ``request.state.user``.
The dependencies here check that attribute first (fast path) and
fall back to direct JWT decode for environments without the
middleware (e.g. unit tests using ``TestClient`` without the full
middleware stack).
"""

from __future__ import annotations

import hmac
import os

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.settings import AppSettings
from ..models.user import User
from ..services.auth import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "testclient"}
_SETUP_TOKEN_HEADER = "x-setup-token"


def _is_loopback_request(request: Request) -> bool:
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client else None
    return host in _LOOPBACK_HOSTS if host else False


def _check_setup_window_origin(request: Request) -> None:
    """Reject bootstrap-window requests from non-loopback hosts that lack a setup token.

    Septum's setup endpoints (test-database, test-redis, initialize,
    install-whisper, ollama-pull, infrastructure PATCH, settings PATCH
    during the first-admin window, ...) used to be reachable from any
    network origin until the first admin was created. That made every
    fresh container an SSRF probe and a settings-poisoning surface for
    any attacker who could reach port 3000 before the legitimate
    operator finished the wizard.

    Now: requests during the bootstrap window must originate from
    loopback OR present ``X-Setup-Token: <SEPTUM_SETUP_TOKEN env>``.
    Once setup completes the bootstrap-window relaxations no longer
    apply and standard admin auth takes over.
    """
    if _is_loopback_request(request):
        return
    expected = os.getenv("SEPTUM_SETUP_TOKEN", "").strip()
    presented = request.headers.get(_SETUP_TOKEN_HEADER, "").strip()
    if expected and presented and hmac.compare_digest(expected, presented):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            "Setup wizard is only reachable from localhost during the "
            "bootstrap window. Set SEPTUM_SETUP_TOKEN and pass it via "
            "the X-Setup-Token header to drive the wizard remotely."
        ),
    )


async def get_current_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Return the authenticated ``User``, or raise 401.

    Fast path: if ``AuthMiddleware`` already resolved the identity,
    load the ``User`` by the stashed ``auth_user_id`` on the route's
    own DB session (avoiding detached-object issues from a separate
    middleware session).
    """
    # Fast path — middleware already identified the user.
    auth_user_id = getattr(request.state, "auth_user_id", None)
    if auth_user_id is not None:
        result = await db.execute(select(User).where(User.id == auth_user_id))
        user = result.scalar_one_or_none()
        if user is not None and user.is_active:
            return user
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    # Fallback — direct JWT decode (tests, middleware-less setups).
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
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Return the current user if a valid token is present, otherwise ``None``.

    Used for endpoints that work both authenticated and anonymously
    (backward compatibility during migration).
    """
    # Fast path — middleware already identified the user.
    auth_user_id = getattr(request.state, "auth_user_id", None)
    if auth_user_id is not None:
        result = await db.execute(select(User).where(User.id == auth_user_id))
        user = result.scalar_one_or_none()
        return user if user is not None and user.is_active else None

    if token is None:
        return None
    try:
        return await get_current_user(request, token, db)
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
    request: Request,
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
    :func:`_is_bootstrap_mode`). During that window the request must
    also originate from loopback OR present a valid
    ``X-Setup-Token: <SEPTUM_SETUP_TOKEN>`` header — otherwise an
    attacker reachable on port 3000 could plant settings, register the
    first admin, and lock the legitimate operator out before they ever
    finish the wizard. Once setup completes, the dependency becomes
    strictly admin-only — equivalent to ``require_role("admin")``.
    Returns ``None`` during bootstrap so handlers that want to use
    the user must handle the optional case.
    """
    if await _is_bootstrap_mode(db):
        _check_setup_window_origin(request)
        return None

    user = await get_current_user(request, token, db)
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions",
        )
    return user


async def require_user_or_bootstrap(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Authenticated under normal operation, anonymous during bootstrap.

    Same bootstrap escape hatch as :func:`require_admin_or_bootstrap`
    but for endpoints that only require *some* authenticated user
    (admin / editor / viewer) outside of first-run setup. Bootstrap
    requests must come from loopback or carry the setup token (see
    :func:`require_admin_or_bootstrap`).
    """
    if await _is_bootstrap_mode(db):
        _check_setup_window_origin(request)
        return None
    return await get_current_user(request, token, db)
