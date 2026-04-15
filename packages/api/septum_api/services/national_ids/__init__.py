"""Backward-compatibility shim.

The implementation of the national ID validators now lives in
:mod:`septum_core.national_ids`. This module re-exports the public API so
existing ``from app.services.national_ids`` imports continue to work.
"""

from __future__ import annotations

from septum_core.national_ids import (  # noqa: F401
    AadhaarValidator,
    BaseIDValidator,
    CPFValidator,
    IBANValidator,
    SSNValidator,
    TCKNValidator,
    ValidationResult,
)

__all__ = [
    "BaseIDValidator",
    "ValidationResult",
    "TCKNValidator",
    "SSNValidator",
    "CPFValidator",
    "AadhaarValidator",
    "IBANValidator",
]
