from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        if _is_api_path(request):
            # API responses carry document text, chat history and
            # de-anonymized answers: keep them out of browser and proxy caches.
            response.headers.setdefault("Cache-Control", "no-store")
            response.headers.setdefault("Pragma", "no-cache")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # Browsers ignore HSTS over plain HTTP, so it is safe to set
        # unconditionally. CSP is scoped to frame-ancestors only — a
        # default-src policy would break the FastAPI /docs Swagger UI
        # which loads from a CDN.
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        return response


def _is_api_path(request: Request) -> bool:
    """Whether the request targets ``/api/``, ignoring any ``--root-path`` mount prefix."""
    path = request.url.path
    root_path = request.scope.get("root_path", "")
    if root_path and (path == root_path or path.startswith(root_path + "/")):
        path = path[len(root_path):]
    return path.startswith("/api/")
