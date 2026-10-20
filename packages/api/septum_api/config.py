"""
Application configuration helpers for Septum.

This module provides a lightweight, synchronous `get_settings` function which
returns the default `AppSettings` row without a database connection. It delegates
to `database.build_default_app_settings`, the single source of truth for defaults,
so scripts and the DB seed path cannot drift apart. It also hosts
`default_ollama_url`, which the database module consumes.
"""

from __future__ import annotations

import os
from pathlib import Path

from .models.settings import AppSettings


def _is_docker() -> bool:
    """Detect if running inside a Docker container."""
    if os.getenv("DOCKER") == "true":
        return True
    return Path("/.dockerenv").exists()


def default_ollama_url() -> str:
    """Return the default Ollama base URL for the current environment."""
    return "http://host.docker.internal:11434" if _is_docker() else "http://localhost:11434"


def get_settings() -> AppSettings:
    """Return an unsaved `AppSettings` built from the seed defaults.

    Delegates to `database.build_default_app_settings`, the same builder the
    DB seed uses, so the result matches the seeded row on every column and no
    database connection is required. The import is local because `database`
    imports `default_ollama_url` from this module.
    """
    from .database import build_default_app_settings

    return build_default_app_settings()
