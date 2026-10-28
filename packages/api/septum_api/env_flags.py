"""Typed ``*_DEFAULT`` environment flags: the single parser and registry.

Kept free of database and ORM imports so that any module, including the
LLM router, can read a flag without pulling in the persistence layer.
"""

from __future__ import annotations

import math
import os
import re

_ENV_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_ENV_FALSE_VALUES = frozenset({"0", "false", "no", "off"})
_ENV_INT_PATTERN = re.compile(r"-?\d{1,18}", re.ASCII)


class EnvFlagError(ValueError):
    """An environment variable holds a value that cannot be parsed or is out of range."""


def _raw_value(name: str) -> str:
    """Return the stripped value of ``name``; unset or whitespace-only gives an empty string."""
    return (os.getenv(name) or "").strip()


def env_bool(name: str, default: bool) -> bool:
    """Parse a boolean environment variable; unset or blank gives ``default``.

    Only ``1/true/yes/on`` and ``0/false/no/off`` (any case) are accepted.
    Anything else raises :class:`EnvFlagError` instead of silently reading as
    false, so a typo in a security flag such as ``USE_GATEWAY_DEFAULT``
    cannot fail open. The message names the variable, never its value.
    """
    value = _raw_value(name).lower()
    if not value:
        return default
    if value in _ENV_TRUE_VALUES:
        return True
    if value in _ENV_FALSE_VALUES:
        return False
    raise EnvFlagError(
        f"{name} must be one of: true, false, 1, 0, yes, no, on, off."
    )


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """Parse an integer environment variable; unset or blank gives ``default``.

    Only an optional sign followed by at most 18 ASCII digits is accepted, and
    the value must lie within ``[minimum, maximum]``. Anything else raises
    :class:`EnvFlagError` rather than falling back to ``default``. The message
    names the variable and its bounds, never its value.
    """
    value = _raw_value(name)
    if not value:
        return default
    if not _ENV_INT_PATTERN.fullmatch(value):
        raise EnvFlagError(f"{name} must be an integer from {minimum} to {maximum}.")
    number = int(value)
    if not minimum <= number <= maximum:
        raise EnvFlagError(f"{name} must be an integer from {minimum} to {maximum}.")
    return number


def env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    """Parse a finite float environment variable within ``[minimum, maximum]``.

    Unset or blank gives ``default``. ``nan`` and ``inf`` are rejected. Any
    failure raises :class:`EnvFlagError` naming the variable, never its value.
    """
    value = _raw_value(name)
    if not value:
        return default
    try:
        number = float(value)
    except ValueError:
        raise EnvFlagError(f"{name} must be a number.") from None
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise EnvFlagError(f"{name} must be a number from {minimum} to {maximum}.")
    return number


BOOL_DEFAULT_FLAGS: tuple[tuple[str, bool], ...] = (
    ("DEANON_ENABLED_DEFAULT", True),
    ("REQUIRE_APPROVAL_DEFAULT", True),
    ("SHOW_JSON_OUTPUT_DEFAULT", False),
    ("USE_PRESIDIO_LAYER_DEFAULT", True),
    ("USE_NER_LAYER_DEFAULT", True),
    ("USE_OLLAMA_VALIDATION_LAYER_DEFAULT", True),
    ("USE_OLLAMA_LAYER_DEFAULT", True),
    ("USE_OLLAMA_SEMANTIC_LAYER_DEFAULT", False),
    ("EXTRACT_EMBEDDED_IMAGES_DEFAULT", True),
    ("RECURSIVE_EMAIL_ATTACHMENTS_DEFAULT", True),
    ("USE_GATEWAY_DEFAULT", False),
)
"""Every boolean ``*_DEFAULT`` env flag with its fallback. Single source of truth."""

_INT_MAX = 2**31 - 1

INT_DEFAULT_FLAGS: tuple[tuple[str, int, int, int], ...] = (
    # (name, default, minimum, maximum)
    ("APPROVAL_TIMEOUT_SECONDS_DEFAULT", 300, 1, _INT_MAX),
    ("CHUNK_SIZE_DEFAULT", 800, 1, _INT_MAX),
    ("CHUNK_OVERLAP_DEFAULT", 200, 0, _INT_MAX),
    ("TOP_K_RETRIEVAL_DEFAULT", 5, 1, _INT_MAX),
    ("PDF_CHUNK_SIZE_DEFAULT", 1200, 1, _INT_MAX),
    ("AUDIO_CHUNK_SIZE_DEFAULT", 60, 1, _INT_MAX),
    ("SPREADSHEET_CHUNK_SIZE_DEFAULT", 200, 1, _INT_MAX),
)
"""Every integer ``*_DEFAULT`` env flag as (name, default, minimum, maximum). Single source of truth."""

FLOAT_DEFAULT_FLAGS: tuple[tuple[str, float, float, float], ...] = (
    # (name, default, minimum, maximum); the relevance score is compared against values in [0, 1].
    ("RAG_RELEVANCE_THRESHOLD_DEFAULT", 0.35, 0.0, 1.0),
)
"""Every float ``*_DEFAULT`` env flag with its fallback and bounds. Single source of truth."""


def parse_bool_default_flags() -> dict[str, bool]:
    """Parse every flag in :data:`BOOL_DEFAULT_FLAGS`; raises :class:`EnvFlagError` on a bad value."""
    return {name: env_bool(name, default) for name, default in BOOL_DEFAULT_FLAGS}


def parse_int_default_flags() -> dict[str, int]:
    """Parse every flag in :data:`INT_DEFAULT_FLAGS`; raises :class:`EnvFlagError` on a bad value."""
    return {
        name: env_int(name, default, minimum, maximum)
        for name, default, minimum, maximum in INT_DEFAULT_FLAGS
    }


def parse_float_default_flags() -> dict[str, float]:
    """Parse every flag in :data:`FLOAT_DEFAULT_FLAGS`; raises :class:`EnvFlagError` on a bad value."""
    return {
        name: env_float(name, default, minimum, maximum)
        for name, default, minimum, maximum in FLOAT_DEFAULT_FLAGS
    }
