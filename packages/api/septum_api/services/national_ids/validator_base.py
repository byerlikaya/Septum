"""Backward-compatibility shim. Implementation moved to septum_core."""

from __future__ import annotations

from septum_core.national_ids.validator_base import (  # noqa: F401
    BaseIDValidator,
    ValidationResult,
)

__all__ = ["BaseIDValidator", "ValidationResult"]
