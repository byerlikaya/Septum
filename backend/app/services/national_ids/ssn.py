"""Backward-compatibility shim. Implementation moved to septum_core."""

from __future__ import annotations

from septum_core.national_ids.ssn import SSNValidator  # noqa: F401

__all__ = ["SSNValidator"]
