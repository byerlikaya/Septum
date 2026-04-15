"""Backward-compat shim — forwards to :mod:`septum_api.utils`.

Phase 3a of the modular refactor moved the infrastructure utility
modules (``crypto``, ``logging_config``, ``metrics``, ``device``,
``db_helpers``, ``text_utils``) into ``packages/api/septum_api/utils/``.
Phase 3b followed by moving ``auth_dependency`` after its
``services.auth`` dependency also migrated. This module re-exports
every ``septum_api.utils`` submodule under the legacy
``backend.app.utils.*`` namespace.
"""

from __future__ import annotations

import pkgutil as _pkgutil
import sys as _sys

import septum_api.utils as _target

from septum_api.utils import *  # noqa: F401,F403

_info = _name = _module = None  # sentinels so ``del`` below never NameErrors
for _info in _pkgutil.iter_modules(_target.__path__):
    _name = _info.name
    _module = __import__(f"septum_api.utils.{_name}", fromlist=["*"])
    _sys.modules[f"{__name__}.{_name}"] = _module
    globals()[_name] = _module

del _info, _name, _module, _pkgutil, _sys, _target
