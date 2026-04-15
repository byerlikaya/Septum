"""Rate limiting configuration and key-function dispatch.

Extracts the inline ``slowapi.Limiter`` setup that lived in ``main.py``
into a reusable factory. The key function inspects
``request.state.auth_method``: API-key requests are rate-limited by
the key's 8-character prefix (so services sharing an IP each get their
own quota), while JWT and anonymous requests fall back to the remote
IP address.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from slowapi import Limiter
from slowapi.util import get_remote_address

if TYPE_CHECKING:
    from starlette.requests import Request

    from ..bootstrap import BootstrapConfig

# sk-septum- is 10 chars; prefix is the next 8
_API_KEY_PREFIX_OFFSET = 10
_API_KEY_PREFIX_END = 18


def get_rate_limit_key(request: Request) -> str:
    """Return a rate-limit key based on the auth method.

    API-key requests → ``apikey:<prefix>`` so each key has an independent
    quota. JWT and anonymous requests → client IP address.
    """
    auth_method = getattr(getattr(request, "state", None), "auth_method", None)
    if auth_method == "api_key":
        api_key_header = request.headers.get("x-api-key", "")
        prefix = (
            api_key_header[_API_KEY_PREFIX_OFFSET:_API_KEY_PREFIX_END]
            if len(api_key_header) >= _API_KEY_PREFIX_END
            else "unknown"
        )
        return f"apikey:{prefix}"
    return get_remote_address(request)


def create_limiter(config: BootstrapConfig) -> Limiter:
    """Build a ``Limiter`` from the bootstrap configuration."""
    storage_uri = (
        f"redis://{config.redis_url.split('://')[-1]}"
        if config.redis_url
        else "memory://"
    )
    return Limiter(
        key_func=get_rate_limit_key,
        default_limits=[config.rate_limit],
        storage_uri=storage_uri,
    )


def get_limiter() -> Limiter:
    """Return the shared ``Limiter`` instance (lazy-created on first call).

    Routers import this to apply per-route ``@limiter.limit()``
    decorators without a circular import through ``main.py``.
    """
    global _limiter  # noqa: PLW0603
    if _limiter is None:
        from ..bootstrap import get_config
        _limiter = create_limiter(get_config())
    return _limiter


_limiter: Limiter | None = None
