"""Backward-compat shim — forwards to :mod:`septum_api.bootstrap`.

Phase 3a of the modular refactor moved the bootstrap configuration
helpers into ``packages/api/septum_api/bootstrap.py``. This module is a
thin re-export that keeps ``from backend.app.bootstrap import ...``
and ``from backend.app import bootstrap`` working for every caller that
has not yet migrated to the new package path.
"""

from __future__ import annotations

from septum_api.bootstrap import *  # noqa: F401,F403

# ``_invalidate_cache`` is the only underscored helper with known
# callers (``backend/tests/test_setup_router.py``) so it is re-exported
# explicitly — ``import *`` skips names that start with an underscore.
from septum_api.bootstrap import _invalidate_cache  # noqa: F401
