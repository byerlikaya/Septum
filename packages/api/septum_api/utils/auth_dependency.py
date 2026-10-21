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
from typing import NoReturn

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import bootstrap
from ..database import engine_is_ready, get_db, get_session_maker
from ..errors import CodedHTTPError
from ..middleware.rate_limit import (
    check_setup_token_failures,
    record_setup_token_failure,
)
from ..models.settings import AppSettings
from ..models.user import User
from ..services.auth import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


SETUP_TOKEN_HEADER = "X-Setup-Token"
SETUP_FORBIDDEN_CODE = "setup_forbidden"
SETUP_FORBIDDEN_DETAIL = "Setup access denied."


def deny_setup_access() -> NoReturn:
    """Raise the one 403 every setup-gate failure shares.

    Missing token, wrong token and closed setup look identical, so a caller
    learns nothing beyond what ``/api/setup/status`` already exposes.
    """
    raise CodedHTTPError(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=SETUP_FORBIDDEN_DETAIL,
        code=SETUP_FORBIDDEN_CODE,
    )


async def require_setup_token(request: Request) -> None:
    """Require ``X-Setup-Token`` to match :func:`bootstrap.get_setup_token` (403 otherwise).

    The only token check in the API. There is no loopback exception: behind
    the dashboard's proxy every remote client reaches the API from loopback,
    so the peer address proves nothing. Whether setup is open is checked by
    the callers, which know whether a database exists yet.

    The token is compared first and a match always passes. Only failures
    are counted, in one per-client bucket shared by every gated route (SSE
    ones included); once it is spent, a wrong token gets 429 instead of 403.
    That bucket is a signal and a brake, not a limit on access: it never
    blocks the correct token, and an attacker spreading guesses over many
    addresses is not stopped by it. The protection is the token itself,
    256 bits when generated, and an operator-set one is refused at startup
    unless it is long and varied enough (``bootstrap.validate_setup_token_env``).
    """
    expected = bootstrap.get_setup_token()
    presented = request.headers.get(SETUP_TOKEN_HEADER, "").strip()
    if (
        expected
        and presented
        and hmac.compare_digest(expected.encode("utf-8"), presented.encode("utf-8"))
    ):
        return
    await check_setup_token_failures(request)
    await record_setup_token_failure(request)
    deny_setup_access()


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


def require_interactive_session(request: Request) -> None:
    """Reject requests authenticated with an API key (403).

    API keys act as their owner, and their ``scopes`` column is reserved
    and not enforced. Account and credential management (API keys, users,
    passwords, provider credentials), masking-policy writes (regulation
    activation, custom recognizers, non-PII rules, text normalization) and
    the setup infrastructure update therefore need an interactive JWT
    session, so a leaked key cannot escalate, weaken masking, or outlive
    its own revocation.
    """
    if getattr(request.state, "auth_method", None) == "api_key":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This operation requires an interactive login session, not an API key",
        )


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


async def is_setup_open(db: AsyncSession) -> bool:
    """Return True while the system is still in first-run bootstrap (database view).

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


async def setup_window_open() -> bool:
    """Return True while setup is open, usable before a database exists.

    With no database configured yet (``bootstrap.needs_setup()``) no user
    can exist, so setup is open. With one configured, the database decides
    through :func:`is_setup_open`. A configured database whose engine is not
    up yet counts as closed rather than guessed open.
    """
    if bootstrap.needs_setup():
        return True
    if not engine_is_ready():
        return False
    async with get_session_maker()() as db:
        return await is_setup_open(db)


async def require_infrastructure_setup(request: Request) -> None:
    """Gate the pre-database wizard steps: infrastructure not configured yet + setup token.

    test-database, test-redis and initialize only make sense before a
    database is chosen; afterwards they answer the same generic 403.
    """
    if not bootstrap.needs_setup():
        deny_setup_access()
    await require_setup_token(request)


async def require_open_setup_token(request: Request) -> None:
    """Gate usable in either wizard phase: :func:`setup_window_open` + setup token."""
    await require_setup_token(request)
    if not await setup_window_open():
        deny_setup_access()


async def require_setup_window(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Gate a setup-time endpoint that has a database: setup open + setup token."""
    if not await is_setup_open(db):
        deny_setup_access()
    await require_setup_token(request)


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
    :func:`is_setup_open`). During that window the request must present
    a valid ``X-Setup-Token`` header (:func:`require_setup_token`) —
    otherwise an attacker reachable on port 3000 could plant settings,
    register the first admin, and lock the legitimate operator out
    before they ever finish the wizard. Once setup completes, the dependency becomes
    strictly admin-only — equivalent to ``require_role("admin")``.
    Returns ``None`` during bootstrap so handlers that want to use
    the user must handle the optional case.
    """
    if await is_setup_open(db):
        await require_setup_token(request)
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
    requests must carry the setup token (see
    :func:`require_admin_or_bootstrap`).
    """
    if await is_setup_open(db):
        await require_setup_token(request)
        return None
    return await get_current_user(request, token, db)
