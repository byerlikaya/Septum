"""Naming scheme of the encrypted source files of uploaded documents."""

from __future__ import annotations

import os
import re
from pathlib import Path
from uuid import uuid4

SOURCE_FILE_DIR = Path(os.getenv("DOCUMENT_STORAGE_DIR", "./documents")).resolve()
"""Directory holding the encrypted source files of uploaded documents."""
SOURCE_FILE_DIR.mkdir(parents=True, exist_ok=True)

_SOURCE_FILE_NAME = re.compile(r"[0-9a-f]{32}")


def new_source_file_name() -> str:
    """Return a fresh random name for an uploaded document's encrypted source file."""
    return uuid4().hex


def is_source_file_name(name: str) -> bool:
    """Return whether ``name`` follows the scheme of :func:`new_source_file_name`."""
    return _SOURCE_FILE_NAME.fullmatch(name) is not None
