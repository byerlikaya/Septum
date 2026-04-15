"""Backward-compat shim — forwards to :mod:`septum_api.bootstrap`.

Phase 3a of the modular refactor moved the bootstrap configuration
helpers into ``packages/api/septum_api/bootstrap.py``. This module is a
thin re-export that keeps ``from backend.app.bootstrap import ...``
and ``from backend.app import bootstrap`` working for every caller that
has not yet migrated to the new package path.
"""

from __future__ import annotations

from septum_api.bootstrap import *  # noqa: F401,F403
from septum_api.bootstrap import (  # noqa: F401 — explicit names for tooling
    _ENV_OVERRIDES,
    _apply_env_overrides,
    _cached_config,
    _config_path,
    _generate_encryption_key,
    _generate_jwt_secret,
    _invalidate_cache,
    _read_config_file,
    _write_config_file,
    BootstrapConfig,
    get_config,
    needs_setup,
    save_config,
)
