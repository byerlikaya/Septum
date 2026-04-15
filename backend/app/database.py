"""Backward-compat shim — forwards to :mod:`septum_api.database`.

Phase 3a of the modular refactor moved the async SQLAlchemy engine,
session maker, and seed helpers into ``packages/api/septum_api/database.py``.
This module is a thin re-export that keeps the existing
``from backend.app.database import get_db, init_db, initialize_engine``
call sites working while the routers and services still live inside
``backend/app/``.
"""

from __future__ import annotations

from septum_api.database import *  # noqa: F401,F403

# ``_engine`` and ``_session_maker`` are deliberately NOT re-exported.
# They are module-level mutable state inside ``septum_api.database`` and
# binding them here would create a stale copy — reads and writes on the
# shim would desync from the real engine. Code that needs to patch the
# engine in tests must target ``septum_api.database`` directly.
