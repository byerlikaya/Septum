"""Septum API package.

Air-gapped FastAPI REST layer for Septum. Phase 3a of the modular
refactor owns the infrastructure primitives — ``bootstrap``, ``config``,
``database``, ``models``, ``seeds``, and most of ``utils`` — that were
previously bundled inside ``backend/app/``. The FastAPI app instance,
routers, services, and middleware still live in ``backend/app/`` and
will migrate in Phase 3b; ``backend/app/`` ships shim modules that
forward to this package so existing imports continue to resolve.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
