"""Septum API middleware package."""

from __future__ import annotations

from .auth import AuthMiddleware

__all__ = ["AuthMiddleware"]
