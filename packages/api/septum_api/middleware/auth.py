"""Unified authentication middleware — lightweight credential resolver.

Inspects incoming requests for a JWT Bearer token or an ``X-API-Key``
header and stashes the resolved identity on ``request.state`` so
downstream FastAPI dependencies can load the full ``User`` on the
route's own DB session (avoiding detached-object issues and double
session overhead).

Sets:
* ``request.state.auth_user_id`` — the authenticated user's PK, or ``None``
* ``request.state.auth_method`` — ``"jwt"`` | ``"api_key"`` | ``None``

The middleware does **not** open a DB session for JWT requests (pure
decode). For API key requests a session is needed to validate the
hash, but the middleware stashes only the ``user_id`` — the full
``User`` load happens downstream on the route's session.

Paths that must be accessible without credentials (health, setup
wizard, OpenAPI docs) are exempt and pass through with ``None``.
"""

from __future__ import annotations

import logging

import jwt
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from ..services.api_key_service import (
    HEADER_NAME as _API_KEY_HEADER,
)
from ..services.api_key_service import (
    PREFIX as _API_KEY_PREFIX,
)

# Starlette normalises header names to lowercase before storing them
# in ``request.headers``, so the lookup key must be lowercase too.
_API_KEY_HEADER_LOWER = _API_KEY_HEADER.lower()

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
    """Resolve Bearer JWT or ``X-API-Key`` header into a user identity."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request.state.auth_user_id = None
        request.state.auth_method = None

        if not request.url.path.startswith(_EXEMPT_PREFIXES):
            await self._resolve_identity(request)

        return await call_next(request)

    async def _resolve_identity(self, request: Request) -> None:
        """Detect credential type and stash the user ID if valid."""
        api_key_header = request.headers.get(_API_KEY_HEADER_LOWER)
        if api_key_header and api_key_header.startswith(_API_KEY_PREFIX):
            await self._resolve_api_key(request, api_key_header)
            return

        auth_header = request.headers.get("authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            self._resolve_jwt(request, auth_header[7:].strip())

    async def _resolve_api_key(self, request: Request, raw_key: str) -> None:
        from ..database import engine_is_ready, get_session_maker
        from ..services.api_key_service import validate_api_key

        if not engine_is_ready():
            return
        sm = get_session_maker()
        if sm is None:
            return
        async with sm() as db:
            user = await validate_api_key(db, raw_key)
            if user is not None:
                request.state.auth_user_id = user.id
                request.state.auth_method = "api_key"

    def _resolve_jwt(self, request: Request, token: str) -> None:
        """Decode the JWT without hitting the database."""
        from ..services.auth import decode_access_token

        try:
            payload = decode_access_token(token)
            request.state.auth_user_id = int(payload["sub"])
            request.state.auth_method = "jwt"
        except (jwt.InvalidTokenError, KeyError, ValueError):
            pass
