"""Backward-compat shim — forwards to :mod:`septum_api.routers`.

Phase 3b of the modular refactor moved every FastAPI router module
(``approval``, ``audit``, ``auth``, ``chat``, ``chat_sessions``,
``chunks``, ``documents``, ``error_logs``, ``regulations``, ``settings``,
``setup``, ``text_normalization``, ``users``) into
``packages/api/septum_api/routers/``. This module re-exports every
migrated submodule under the legacy ``backend.app.routers.*`` namespace.

Unlike ``services``, routers has no nested subpackages, so the shallow
``pkgutil.iter_modules`` pattern from Phase 3a is sufficient — each
direct child of ``septum_api.routers.__path__`` is imported once at
shim-load time and registered in ``sys.modules`` under the legacy path.
"""

from __future__ import annotations

import pkgutil as _pkgutil
import sys as _sys

import septum_api.routers as _target

from septum_api.routers import *  # noqa: F401,F403

_info = _name = _module = None  # sentinels so ``del`` below never NameErrors
for _info in _pkgutil.iter_modules(_target.__path__):
    _name = _info.name
    _module = __import__(f"septum_api.routers.{_name}", fromlist=["*"])
    _sys.modules[f"{__name__}.{_name}"] = _module
    globals()[_name] = _module

del _info, _name, _module, _pkgutil, _sys, _target
