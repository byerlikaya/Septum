"""Septum core: privacy-first PII detection, masking, and unmasking engine.

This package is the air-gapped heart of Septum. It never imports network
libraries (no httpx, requests, urllib, aiohttp) and never talks to a
database directly. All optional LLM-assisted features are injected
through protocols defined inside this package.

The full public API (``SeptumEngine``) becomes available as subsequent
extraction phases land. For now the package exposes the building blocks
that have already been extracted from the monolithic backend.
"""

from __future__ import annotations

from .anonymization_map import AnonymizationMap
from .national_ids import (
    AadhaarValidator,
    BaseIDValidator,
    CPFValidator,
    IBANValidator,
    SSNValidator,
    TCKNValidator,
    ValidationResult,
)

__all__ = [
    "AnonymizationMap",
    "BaseIDValidator",
    "ValidationResult",
    "TCKNValidator",
    "SSNValidator",
    "CPFValidator",
    "AadhaarValidator",
    "IBANValidator",
]

__version__ = "0.1.0"
