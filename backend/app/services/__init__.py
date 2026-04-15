"""Backward-compat shim — forwards to :mod:`septum_api.services`.

Phase 3b of the modular refactor moved every service module (top-level
files plus the ``ingestion``, ``llm_providers``, ``national_ids``, and
``recognizers`` subpackages) into ``packages/api/septum_api/services/``.

Unlike the shallow ``pkgutil.iter_modules`` pattern used for the Phase 3a
``models`` / ``seeds`` / ``utils`` shims, services has nested subpackages
and that pattern would only register direct children. Python would then
re-import nested files like ``ingestion/pdf_ingester.py`` under the shim
namespace, producing duplicate module objects that break ``isinstance``
checks and share nothing with the real singletons.

Eagerly walking the whole tree would fix the duplication but at a steep
cost: ``pdf_ingester`` pulls in PyMuPDF, ``audio_ingester`` pulls in
ffmpeg, ``vector_store`` pulls in faiss + sentence-transformers,
``paddle_provider`` spins up PaddleOCR. Loading them at shim-import time
would add ~5–10 s of startup for any caller that only wants a single
service.

Instead we install a ``sys.meta_path`` finder that translates the shim
namespace to the real namespace *on demand*. Heavy ML imports still fire
only when the caller actually touches those modules, and every file is
still represented by exactly one module object.
"""

from __future__ import annotations

import importlib as _importlib
import importlib.machinery as _machinery
import sys as _sys
import warnings as _warnings

import septum_api.services as _target

# Aliasing a real module under a shim name causes a mismatch between
# ``module.__package__`` (real parent) and ``module.__spec__.parent`` (shim
# parent) because Python's ``_init_module_attrs`` replaces ``__spec__`` but
# leaves ``__package__`` alone when it is already set. Python 3.12+ emits a
# ``DeprecationWarning`` each time the module then executes a relative
# import. The mismatch is cosmetic — relative imports resolve correctly
# via ``__package__`` and reach the same real modules — so we silence the
# specific warning here rather than mutating ``__package__`` on shared
# module objects (which would ripple through every consumer on the real
# import path).
_warnings.filterwarnings(
    "ignore",
    message=r"__package__ != __spec__\.parent",
    category=DeprecationWarning,
)

_SHIM_PREFIX = __name__ + "."
_TARGET_PREFIX = _target.__name__ + "."


class _AliasLoader:
    """Return an already-imported module object without re-executing it."""

    def __init__(self, real_module):
        self._real_module = real_module

    def create_module(self, spec):  # noqa: D401 - importlib protocol
        return self._real_module

    def exec_module(self, module):  # noqa: D401 - importlib protocol
        # The real module was executed during ``import_module`` in the
        # finder, so there is nothing left to run here.
        return None


class _ServicesAliasFinder:
    """Map ``backend.app.services.*`` → ``septum_api.services.*``.

    Installed at position 0 of ``sys.meta_path`` so it intercepts every
    import under the shim namespace before the default finders run. This
    includes nested imports like ``app.services.ingestion.pdf_ingester``,
    which the default finder would otherwise resolve against
    ``septum_api.services.ingestion.__path__`` and re-load under the shim
    name, producing a duplicate module object.
    """

    def find_spec(self, fullname, path=None, target=None):  # noqa: D401
        if not fullname.startswith(_SHIM_PREFIX):
            return None
        real_name = _TARGET_PREFIX + fullname[len(_SHIM_PREFIX):]
        real_module = _importlib.import_module(real_name)

        # Do NOT pre-register ``sys.modules[fullname]`` here. CPython's
        # ``_find_spec`` has a post-finder check: if the finder returns a
        # spec *and* ``name in sys.modules``, it discards our spec and
        # substitutes ``sys.modules[name].__spec__`` (the reload-safety
        # fallback). Because the pre-registered module is the real one,
        # its ``__spec__`` points at the real ``SourceFileLoader``, and
        # ``_load_unlocked`` then re-runs the file and produces a second
        # module object — the exact duplication we are trying to avoid.
        # Leaving ``sys.modules[fullname]`` unset lets ``_load_unlocked``
        # itself populate it via ``spec.name`` with the correct object.
        real_spec = real_module.__spec__
        spec = _machinery.ModuleSpec(
            fullname,
            _AliasLoader(real_module),
            origin=getattr(real_spec, "origin", None),
            is_package=hasattr(real_module, "__path__"),
        )
        if hasattr(real_module, "__path__"):
            spec.submodule_search_locations = list(real_module.__path__)
        return spec


_sys.meta_path.insert(0, _ServicesAliasFinder())

# The underscored module-level helpers above (``_importlib``,
# ``_machinery``, ``_sys``, ``_target``, ``_SHIM_PREFIX``,
# ``_TARGET_PREFIX``) are intentionally left in place — the finder's
# methods reference them as globals at call time, so deleting them here
# would turn the first post-shim-load import into a ``NameError``.
# The leading underscore keeps them out of ``from ... import *``.
