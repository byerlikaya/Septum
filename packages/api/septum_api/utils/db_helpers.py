"""Shared database and validation helpers for Septum routers."""
from __future__ import annotations

import logging
import os
import re
import threading
from functools import lru_cache
from typing import Any, FrozenSet, List, Optional, Type, TypeVar

from fastapi import HTTPException, status
from langdetect import detect_langs, detector_factory
from langdetect.lang_detect_exception import ErrorCode, LangDetectException
from septum_core.language_resolution import (
    detection_sample,
    iso_language_code,
    language_table_codes,
    resolve_language,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.settings import AppSettings

T = TypeVar("T")

logger = logging.getLogger(__name__)


async def get_or_404(
    db: AsyncSession,
    model: Type[T],
    pk: int,
    detail: str = "Resource not found.",
) -> T:
    """Fetch a row by primary key or raise HTTP 404."""
    result = await db.execute(select(model).where(model.id == pk))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return row


async def get_owned_or_404(
    db: AsyncSession,
    model: Type[T],
    pk: int,
    user: Any,
    detail: str = "Resource not found.",
) -> T:
    """Fetch a row by primary key, asserting ownership.

    Returns 404 (never 403) on ownership mismatch so non-owner users
    cannot enumerate which IDs exist for other users. ``admin`` role
    bypasses the check by design — the system operator can read any
    artifact for support purposes.
    """
    result = await db.execute(select(model).where(model.id == pk))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    if getattr(user, "role", None) == "admin":
        return row
    owner_id = getattr(row, "user_id", None)
    if owner_id is None or owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return row


async def load_settings(db: AsyncSession) -> AppSettings:
    """Load the singleton AppSettings row, lazy-seeding it if missing.

    A missing ``AppSettings`` row used to raise HTTP 500 ("Application
    settings have not been initialized"), which piled up in the Error
    Logs UI as a storm of identical 500s whenever the Settings page was
    polled during a transient bootstrap window. Every wizard endpoint
    (``GET``/``PATCH /api/settings``, ``/test-llm``, ``/test-local-models``)
    also goes through this helper, so raising here would leave the
    wizard unable to make progress — it needs a row to update.

    The helper is now idempotent: if the row is missing, build the
    default one via :func:`database.build_default_app_settings` — the
    same factory ``_seed_defaults`` calls at startup — commit it, and
    return it. Fresh databases where the lifespan skipped ``init_db``
    (for example because ``bootstrap.needs_setup()`` returned ``True``
    at startup) self-heal on the first read instead of 500-spamming
    every request until the wizard finishes.
    """
    result = await db.execute(select(AppSettings).where(AppSettings.id == 1))
    settings = result.scalar_one_or_none()
    if settings is None:
        from ..database import build_default_app_settings

        settings = build_default_app_settings()
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


_language_profiles_lock = threading.Lock()
_loaded_language_factory: Optional[object] = None


def ensure_language_profiles_loaded() -> None:
    """Load langdetect's language profiles exactly once, thread-safely.

    ``langdetect`` publishes its global factory before the profiles are
    loaded and has no lock, so two first calls in parallel can see an
    empty factory and fail with "Need to load profiles". The lifespan
    calls this at startup; :func:`detect_language` calls it as well so
    tests and scripts that skip the lifespan are covered. Relies on
    langdetect 1.0.9 internals (``detector_factory._factory``), pinned in
    ``requirements.txt``.
    """
    global _loaded_language_factory
    if (
        _loaded_language_factory is not None
        and _loaded_language_factory is detector_factory._factory
    ):
        return
    with _language_profiles_lock:
        if (
            _loaded_language_factory is None
            or _loaded_language_factory is not detector_factory._factory
        ):
            detector_factory.init_factory()
            _require_complete_profiles(detector_factory._factory)
            _loaded_language_factory = detector_factory._factory


def _require_complete_profiles(factory: Any) -> None:
    """Raise unless ``factory`` holds one language per bundled profile file.

    ``load_profile`` publishes the factory before it reads the files, so a
    load that failed midway leaves a short language list that later calls
    would use silently. The short factory is discarded so a later call
    loads afresh instead of reusing it. Relies on langdetect 1.0.9 internals
    (``detector_factory._factory``, ``PROFILES_DIRECTORY``), pinned in
    ``requirements.txt``.
    """
    expected = len(_profile_names(detector_factory.PROFILES_DIRECTORY))
    loaded = len(factory.get_lang_list()) if factory is not None else 0
    if expected == 0 or loaded != expected:
        detector_factory._factory = None
        raise LangDetectException(
            ErrorCode.NeedLoadProfileError,
            f"langdetect loaded {loaded} of {expected} language profiles",
        )


def _profile_names(directory: str) -> List[str]:
    """Names of the profile files ``load_profile`` reads from ``directory``."""
    return [
        name
        for name in os.listdir(directory)
        if not name.startswith(".") and os.path.isfile(os.path.join(directory, name))
    ]


@lru_cache(maxsize=4)
def _supported_codes(directory: str) -> FrozenSet[str]:
    """Canonical codes of the profiles in ``directory`` plus septum-core's table keys."""
    profiles = (code for code in map(iso_language_code, _profile_names(directory)) if code)
    return frozenset(profiles) | language_table_codes()


def supported_language_codes() -> FrozenSet[str]:
    """ISO 639 codes Septum knows: those detection can produce and those its tables key.

    ``zh-cn`` and ``zh-tw`` profiles count as ``zh``; a language with a
    table entry but no detection profile (``az``) is known too.
    """
    return _supported_codes(detector_factory.PROFILES_DIRECTORY)


def supported_language_code(code: Optional[str]) -> Optional[str]:
    """``code`` canonicalized if it is a supported ISO 639 code, else ``None``."""
    return iso_language_code(code, supported_language_codes())


def document_language(document: Any) -> Optional[str]:
    """The document's language: a valid override first, then a valid detected code.

    A stored value that is malformed or names a language detection does
    not support (written before the override was validated) is treated as
    absent rather than used as a language.
    """
    return supported_language_code(document.language_override) or supported_language_code(
        document.detected_language
    )


def detect_language(
    text: str,
    fallback: str = "en",
    context_language: Optional[str] = None,
) -> str:
    """Detect the ISO 639-1 language of ``text``.

    langdetect's ranked guesses go through
    :func:`septum_core.language_resolution.resolve_language`, which
    canonicalizes region tags (``zh-cn`` -> ``zh``) and, for a weak or
    script-contradicted guess, prefers ``context_language`` (the language
    of the document a query targets), then the script-implied language,
    then ``fallback``. Both langdetect and the script check read the same
    bounded :func:`~septum_core.language_resolution.detection_sample`.
    Text langdetect finds no features in (empty, digits only) resolves the
    same way with no guesses; every other langdetect error propagates.
    """
    ensure_language_profiles_loaded()
    sample = detection_sample(text)
    try:
        candidates = [(guess.lang, guess.prob) for guess in detect_langs(sample)]
    except LangDetectException as exc:
        if exc.get_code() != ErrorCode.CantDetectError:
            raise
        candidates = []
    language = resolve_language(
        sample, candidates, context_language=context_language, default=fallback
    )
    if not candidates and any(ch.isalpha() for ch in sample):
        logger.warning(
            "Language detection found no features in lettered text; fell back to %s",
            language,
        )
    return language


def validate_regex(pattern: str) -> Optional[str]:
    """Validate a regex pattern. Returns error message or None if valid."""
    try:
        re.compile(pattern)
        return None
    except re.error as exc:
        return str(exc)
