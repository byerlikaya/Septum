"""Backward-compat shim — forwards to :mod:`septum_api.models`.

Phase 3a of the modular refactor moved every ORM model (including the
``Base`` declarative class) into ``packages/api/septum_api/models/``.
This module keeps the classic ``from backend.app.models import Base``
and ``from backend.app.models.settings import AppSettings`` imports
working by auto-registering each ``septum_api.models`` submodule in
``sys.modules`` under the ``backend.app.models`` namespace.

When routers and services migrate to ``septum_api`` in Phase 3b this
shim becomes dead weight and can be deleted.
"""

from __future__ import annotations

import pkgutil as _pkgutil
import sys as _sys

import septum_api.models as _target

from septum_api.models import *  # noqa: F401,F403
from septum_api.models import Base  # noqa: F401

# Register every concrete submodule (``settings``, ``user``, …) under the
# legacy ``backend.app.models`` path so that dotted imports like
# ``from backend.app.models.settings import AppSettings`` resolve via
# ``sys.modules`` without needing physical files on disk.
_info = _name = _module = None  # sentinels so ``del`` below never NameErrors
for _info in _pkgutil.iter_modules(_target.__path__):
    _name = _info.name
    _module = __import__(f"septum_api.models.{_name}", fromlist=["*"])
    _sys.modules[f"{__name__}.{_name}"] = _module
    globals()[_name] = _module

del _info, _name, _module, _pkgutil, _sys, _target
