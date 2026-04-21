"""Septum API — air-gapped FastAPI REST layer.

Submodules (``bootstrap``, ``config``, ``database``, ``models``,
``routers``, ``services``, ``middleware``, ``utils``, ``seeds``) are
imported directly; the package ``__init__`` is deliberately empty so a
bare ``import septum_api`` stays cheap.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
