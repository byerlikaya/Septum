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
from septum_api.database import (  # noqa: F401 — explicit names for tooling
    _builtin_regulations,
    _csv_env_to_list,
    _engine,
    _engine_kwargs,
    _env_bool,
    _env_int,
    _seed_defaults,
    _session_maker,
    _sqlite_ensure_columns,
    _sqlite_wal_connect,
    build_database_url,
    build_default_app_settings,
    engine_is_ready,
    get_db,
    get_engine,
    get_session_maker,
    init_db,
    initialize_engine,
    is_sqlite,
)
