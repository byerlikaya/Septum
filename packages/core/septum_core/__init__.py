"""Septum core: privacy-first PII detection, masking, and unmasking engine.

This package is the air-gapped heart of Septum. It never imports network
libraries (no httpx, requests, urllib, aiohttp) and never talks to a
database directly. All optional LLM-assisted features are injected
through protocols defined inside this package.

The public entry point is :class:`SeptumEngine`, which wraps the
detector, unmasker, recognizer registry and a session store for
round-tripping masked text through external LLMs without ever
exposing raw PII off the host.

Heavy symbols that pull in ``transformers`` / ``torch``
(:class:`SeptumEngine`, :class:`Detector`, :class:`NERModelRegistry`) are
exposed via a lazy :pep:`562` ``__getattr__`` hook so that hosts which
install the base package without the ``[transformers]`` extra can still
import the lightweight composer / recognizer primitives.
"""

from __future__ import annotations

from typing import Any

from .anonymization_map import AnonymizationMap
from .config import SeptumCoreConfig
from .national_ids import (
    AadhaarValidator,
    BaseIDValidator,
    CPFValidator,
    IBANValidator,
    SSNValidator,
    TCKNValidator,
    ValidationResult,
)
from .non_pii_filter import NonPiiFilter, SpanView
from .ports import NullSemanticDetectionPort, SemanticDetectionPort
from .recognizers import RecognizerRegistry
from .regulations import (
    CustomRecognizerLike,
    NonPiiRuleLike,
    RegulationRulesetLike,
)
from .regulations.composer import ComposedPolicy, PolicyComposer
from .spans import DetectedSpan, ResolvedSpan, SanitizeResult
from .unmasker import Unmasker

__all__ = [
    "SeptumEngine",
    "MaskResult",
    "AnonymizationMap",
    "Detector",
    "NERModelRegistry",
    "Unmasker",
    "BaseIDValidator",
    "ValidationResult",
    "TCKNValidator",
    "SSNValidator",
    "CPFValidator",
    "AadhaarValidator",
    "IBANValidator",
    "SeptumCoreConfig",
    "SemanticDetectionPort",
    "NullSemanticDetectionPort",
    "RecognizerRegistry",
    "ComposedPolicy",
    "PolicyComposer",
    "RegulationRulesetLike",
    "CustomRecognizerLike",
    "NonPiiRuleLike",
    "NonPiiFilter",
    "SpanView",
    "DetectedSpan",
    "ResolvedSpan",
    "SanitizeResult",
]

__version__ = "0.1.0"


def __getattr__(name: str) -> Any:
    """Lazy resolver for symbols that pull in the optional ``transformers`` extra.

    Keeps the base ``import septum_core`` path light so hosts which only
    need the masking primitives don't pay the cost of loading
    ``transformers`` / ``torch`` at import time.
    """
    if name == "SeptumEngine" or name == "MaskResult":
        from .engine import MaskResult, SeptumEngine

        return {"SeptumEngine": SeptumEngine, "MaskResult": MaskResult}[name]
    if name == "Detector":
        from .detector import Detector

        return Detector
    if name == "NERModelRegistry":
        from .ner_model_registry import NERModelRegistry

        return NERModelRegistry
    raise AttributeError(f"module 'septum_core' has no attribute {name!r}")
