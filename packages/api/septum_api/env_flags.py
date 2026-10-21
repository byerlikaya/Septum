"""Boolean ``*_DEFAULT`` environment flags: the single parser and registry.

Kept free of database and ORM imports so that any module, including the
LLM router, can read a flag without pulling in the persistence layer.
"""

from __future__ import annotations

import os

_ENV_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_ENV_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


class EnvFlagError(ValueError):
    """A boolean environment variable holds an unrecognised value."""


def env_bool(name: str, default: bool) -> bool:
    """Parse a boolean environment variable; unset or blank gives ``default``.

    Only ``1/true/yes/on`` and ``0/false/no/off`` (any case) are accepted.
    Anything else raises :class:`EnvFlagError` instead of silently reading as
    false, so a typo in a security flag such as ``USE_GATEWAY_DEFAULT``
    cannot fail open. The message names the variable, never its value.
    """
    value = (os.getenv(name) or "").strip().lower()
    if not value:
        return default
    if value in _ENV_TRUE_VALUES:
        return True
    if value in _ENV_FALSE_VALUES:
        return False
    raise EnvFlagError(
        f"{name} must be one of: true, false, 1, 0, yes, no, on, off."
    )


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


def parse_bool_default_flags() -> dict[str, bool]:
    """Parse every flag in :data:`BOOL_DEFAULT_FLAGS`; raises :class:`EnvFlagError` on a bad value."""
    return {name: env_bool(name, default) for name, default in BOOL_DEFAULT_FLAGS}
