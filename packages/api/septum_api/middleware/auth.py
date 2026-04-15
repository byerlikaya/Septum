"""Unified authentication middleware — resolves JWT and API key credentials.

Sets ``request.state.user`` to the authenticated ``User`` (or ``None``)
and ``request.state.auth_method`` to ``"jwt"``, ``"api_key"``, or ``None``
so downstream FastAPI dependencies can skip redundant decode work.

Paths that must be accessible without credentials (health, setup wizard,
OpenAPI docs) are exempt and always pass through with ``user=None``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import jwt
from fastapi import Request
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from ..services.api_key_service import PREFIX as _API_KEY_PREFIX

if TYPE_CHECKING:
    from ..models.user import User

logger = logging.getLogger(__name__)

_EXEMPT_PREFIXES = (
    "/health",
    "/metrics",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/api/setup/",
    "/api/auth/login",
    "/api/auth/register",
)


class AuthMiddleware(BaseHTTPMiddleware):
    """Resolve Bearer JWT or ``X-API-Key`` header into a ``User``."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request.state.user = None
        request.state.auth_method = None

        path = request.url.path
        if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            return await call_next(request)

        user = await self._resolve_user(request)
        if user is not None:
            request.state.user = user

        return await call_next(request)

    async def _resolve_user(self, request: Request) -> User | None:
        """Try API key first (cheap prefix check), then JWT."""
        # --- API key path ---
        api_key_header = request.headers.get("x-api-key")
        if api_key_header and api_key_header.startswith(_API_KEY_PREFIX):
            return await self._resolve_api_key(request, api_key_header)

        # --- JWT Bearer path ---
        auth_header = request.headers.get("authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
            return await self._resolve_jwt(request, token)

        return None

    async def _resolve_api_key(self, request: Request, raw_key: str) -> User | None:
        from ..database import engine_is_ready, get_session_maker
        from ..services.api_key_service import validate_api_key

        if not engine_is_ready():
            return None
        sm = get_session_maker()
        if sm is None:
            return None
        async with sm() as db:
            user = await validate_api_key(db, raw_key)
            if user is not None:
                request.state.auth_method = "api_key"
            return user

    async def _resolve_jwt(self, request: Request, token: str) -> User | None:
        from ..database import engine_is_ready, get_session_maker
        from ..models.user import User as UserModel
        from ..services.auth import decode_access_token

        try:
            payload = decode_access_token(token)
            user_id = int(payload["sub"])
        except (jwt.InvalidTokenError, KeyError, ValueError):
            return None

        if not engine_is_ready():
            return None
        sm = get_session_maker()
        if sm is None:
            return None
        async with sm() as db:
            result = await db.execute(
                select(UserModel).where(UserModel.id == user_id)
            )
            user = result.scalar_one_or_none()
            if user is not None and user.is_active:
                request.state.auth_method = "jwt"
                return user
        return None
