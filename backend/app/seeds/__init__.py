"""Backward-compat shim — forwards to :mod:`septum_api.seeds`.

Phase 3a of the modular refactor moved the built-in regulation seed
data into ``packages/api/septum_api/seeds/``. This module preserves
``from backend.app.seeds.regulations import builtin_regulations`` by
registering every ``septum_api.seeds`` submodule in ``sys.modules``
under the ``backend.app.seeds`` namespace.
"""

from __future__ import annotations

import pkgutil as _pkgutil
import sys as _sys

import septum_api.seeds as _target

from septum_api.seeds import *  # noqa: F401,F403

_info = _name = _module = None  # sentinels so ``del`` below never NameErrors
for _info in _pkgutil.iter_modules(_target.__path__):
    _name = _info.name
    _module = __import__(f"septum_api.seeds.{_name}", fromlist=["*"])
    _sys.modules[f"{__name__}.{_name}"] = _module
    globals()[_name] = _module

del _info, _name, _module, _pkgutil, _sys, _target
