"""Septum API package.

Air-gapped FastAPI REST layer for Septum. The Phase 3a scaffold ships
``bootstrap``, ``config``, ``database``, ``models``, ``seeds``, and most
of ``utils`` as importable submodules — callers reach them via
``from septum_api import bootstrap`` or ``from septum_api.database import
get_db``. This ``__init__`` deliberately does not re-export any of
their contents so importing the package stays cheap; the real FastAPI
``app`` instance, routers, services, and middleware still live in
``backend/app/`` and will migrate in Phase 3b. ``backend/app/`` ships
shim modules that forward to this package so existing imports keep
resolving without any call-site edits.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
