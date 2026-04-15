"""Backward-compat shim — forwards to :mod:`septum_api.config`.

Phase 3a of the modular refactor moved the synchronous ``get_settings``
helper (and the environment parsing helpers that back it) into
``packages/api/septum_api/config.py``. This module keeps the existing
``from backend.app.config import default_ollama_url`` and
``from backend.app.config import get_settings`` import paths working.
"""

from __future__ import annotations

from septum_api.config import *  # noqa: F401,F403
from septum_api.config import (  # noqa: F401 — explicit names for tooling
    _csv_env_to_list,
    _env_bool,
    _env_int,
    _is_docker,
    default_ollama_url,
    get_settings,
)
