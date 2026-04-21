"""Backward-compatibility shim. Implementation moved to septum_core."""

from __future__ import annotations

from septum_core.national_ids.aadhaar import AadhaarValidator  # noqa: F401

__all__ = ["AadhaarValidator"]
