"""Backward-compat shim — forwards to :mod:`septum_api.main`.

Phase 3b of the modular refactor moved the FastAPI app factory,
lifespan handler, middleware wiring, and top-level exception handlers
from ``backend/app/main.py`` into ``packages/api/septum_api/main.py``.
This module re-exports the public symbols so callers that still use
the legacy import path keep working:

* ``from app.main import app`` — used by the test suite fixtures and
  the uvicorn entry point (``uvicorn app.main:app``) in ``dev.sh``.
* ``from app.main import limiter`` — used by rate-limit-aware tests.

The ``app`` instance, ``limiter``, and every other top-level binding
forwarded by the wildcard re-export resolve to the exact same objects
that live in ``septum_api.main``, so there is only one FastAPI
application in the process regardless of which path imported it.
"""

from __future__ import annotations

from septum_api.main import *  # noqa: F401,F403
