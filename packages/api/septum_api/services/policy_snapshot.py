"""Per-document policy snapshot and read-time staleness.

A document records the policy its chunks were sanitized under: the sorted
entity types (``Document.sanitized_entity_types``) and a digest
(``Document.sanitized_policy_digest``) of what decides detection beyond the
types: the detector version of septum-core, the definitions of the active
custom recognizers and non-PII (allow-list) rules, and the settings that
decide which detection layers run (see :func:`detection_settings`). A
completed document is stale when

* the current policy names an entity type the snapshot lacks (a snapshot with
  more types than the current policy never flags: over-masking is safe), or
* the digest is missing or differs from the current one (a new detector
  version, a custom recognizer added, edited or removed, a non-PII rule
  added, edited or removed — removing one is the case that matters, since
  the current policy then masks values the stored chunks left in clear — or
  a detection layer, NER model or Ollama validation model changed).

The entity types are deliberately left out of the digest: they are compared
as a set above, so deactivating a regulation (fewer types) does not flag.

Only completed documents are judged: a processing or failed document has no
finished chunks to judge.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional, Sequence

from septum_core import DETECTOR_VERSION
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import build_default_app_settings
from ..models.document import Document
from ..models.regulation import CustomRecognizer, NonPiiRule, RegulationRuleset
from ..models.settings import AppSettings
from ..utils.db_helpers import effective_masking_language

_CUSTOM_DEFINITION_COLUMNS = (
    CustomRecognizer.entity_type,
    CustomRecognizer.detection_method,
    CustomRecognizer.pattern,
    CustomRecognizer.keywords,
    CustomRecognizer.llm_prompt,
    CustomRecognizer.context_words,
)

_DETECTION_SETTING_COLUMNS = (
    AppSettings.use_presidio_layer,
    AppSettings.use_ner_layer,
    AppSettings.use_ollama_validation_layer,
    AppSettings.use_ollama_semantic_layer,
    AppSettings.ner_model_overrides,
    AppSettings.ollama_deanon_model,
)

_NON_PII_DEFINITION_COLUMNS = (
    NonPiiRule.pattern_type,
    NonPiiRule.pattern,
    NonPiiRule.languages,
    NonPiiRule.entity_types,
    NonPiiRule.min_score,
)


@dataclass(frozen=True)
class PolicySnapshot:
    """What a document records about the policy it was sanitized under."""

    entity_types: List[str]
    digest: str

    @property
    def cache_key(self) -> str:
        """Identity of the whole policy (digest and entity types), for caching.

        The staleness digest leaves the entity types out on purpose (they are
        compared as a set), so anything cached per policy must key on this
        instead: a newly activated type changes it.
        """
        canonical = json.dumps([self.digest, self.entity_types], ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DocumentStaleness:
    """Read-time staleness of one document against the current policy."""

    needs_reprocessing: bool = False
    missing_entity_types: List[str] = field(default_factory=list)
    sanitized_policy_known: bool = True


def snapshot_entity_types(entity_types: Iterable[str]) -> List[str]:
    """Return the snapshot form of a policy's entity types: unique and sorted."""
    return sorted(set(entity_types))


def custom_recognizer_definition(recognizer: Any) -> tuple:
    """Return the fields of a custom recognizer that decide what it detects."""
    return (
        recognizer.entity_type,
        recognizer.detection_method,
        recognizer.pattern,
        recognizer.keywords,
        recognizer.llm_prompt,
        recognizer.context_words,
    )


def non_pii_rule_definition(rule: Any) -> tuple:
    """Return the fields of a non-PII rule that decide what it lets through."""
    return (
        rule.pattern_type,
        rule.pattern,
        rule.languages,
        rule.entity_types,
        rule.min_score,
    )


def detection_settings(settings: Any) -> tuple:
    """Return the settings that decide which spans the document sanitizer finds.

    Included: ``use_presidio_layer``, ``use_ner_layer``,
    ``use_ollama_validation_layer``, ``use_ollama_semantic_layer`` (the
    contextual layer), ``ner_model_overrides``, and ``ollama_deanon_model``,
    the model the validation and contextual layers call, which decides which
    candidate spans survive; the model counts only while one of those two
    layers is on. Left out: Ollama endpoint URLs, and ``use_ollama_layer``
    (semantic detection), which the sanitizer factory fixes off for document
    text (``DOCUMENT_SANITIZER_ENABLE_OLLAMA``).
    """
    validation = bool(settings.use_ollama_validation_layer)
    contextual = bool(settings.use_ollama_semantic_layer)
    return (
        bool(settings.use_presidio_layer),
        bool(settings.use_ner_layer),
        validation,
        contextual,
        sorted((settings.ner_model_overrides or {}).items()),
        settings.ollama_deanon_model if validation or contextual else None,
    )


def _canonical(definitions: Iterable[Sequence[Any]]) -> List[str]:
    return sorted(
        json.dumps(list(d), sort_keys=True, ensure_ascii=False) for d in definitions
    )


def build_policy_snapshot(
    regulation_entity_types: Iterable[Optional[Sequence[str]]],
    custom_definitions: Iterable[Sequence[Any]],
    non_pii_definitions: Iterable[Sequence[Any]] = (),
    detection: Optional[Sequence[Any]] = None,
) -> PolicySnapshot:
    """Build the snapshot of a policy from its active rows.

    The entity types are the union the policy composer builds: every active
    regulation's types plus each active custom recognizer's type. The digest
    is the SHA-256 of a canonical JSON form of the detector version, the
    sorted custom recognizer and non-PII rule definitions and the detection
    settings (``detection``, from :func:`detection_settings`). ``None``
    stands for the default settings of a fresh install; production callers
    always pass the settings row their sanitizer uses.
    """
    if detection is None:
        detection = detection_settings(build_default_app_settings())
    definitions = [list(d) for d in custom_definitions]
    types: set[str] = {d[0] for d in definitions}
    for entity_types in regulation_entity_types:
        types.update(entity_types or [])
    entity_types = snapshot_entity_types(types)
    canonical = json.dumps(
        {
            "detector": DETECTOR_VERSION,
            "custom": _canonical(definitions),
            "non_pii": _canonical(non_pii_definitions),
            "settings": list(detection),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return PolicySnapshot(
        entity_types=entity_types,
        digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


async def current_policy_snapshot(db: AsyncSession) -> PolicySnapshot:
    """Return the snapshot of the current effective policy.

    Four column selects; no recognizer is built. Without a settings row the
    defaults are used, as :func:`load_settings` would seed them.
    """
    regulation_rows = await db.execute(
        select(RegulationRuleset.entity_types).where(
            RegulationRuleset.is_active.is_(True)
        )
    )
    custom_rows = await db.execute(
        select(*_CUSTOM_DEFINITION_COLUMNS).where(CustomRecognizer.is_active.is_(True))
    )
    non_pii_rows = await db.execute(
        select(*_NON_PII_DEFINITION_COLUMNS).where(NonPiiRule.is_active.is_(True))
    )
    settings_row = (
        await db.execute(select(*_DETECTION_SETTING_COLUMNS).where(AppSettings.id == 1))
    ).first()
    detection = detection_settings(
        settings_row if settings_row is not None else build_default_app_settings()
    )
    return build_policy_snapshot(
        regulation_rows.scalars().all(),
        [tuple(r) for r in custom_rows.all()],
        [tuple(r) for r in non_pii_rows.all()],
        detection=detection,
    )


async def current_policy_entity_types(db: AsyncSession) -> List[str]:
    """Return the entity types of the current effective policy."""
    return (await current_policy_snapshot(db)).entity_types


def document_staleness(
    document: Document, current: Optional[PolicySnapshot]
) -> DocumentStaleness:
    """Judge ``document`` against the ``current`` policy snapshot.

    ``current`` may be ``None`` only when the caller knows the document is
    not completed (no policy read is needed then). The policy is known only
    when both the entity types and the digest were recorded; an unknown
    policy reports no missing types, since every type would be listed.
    A completed document is also outdated when the language it was masked
    in (``sanitized_language``, ``None`` when unknown) is not the language
    it should be masked in now (:func:`effective_masking_language`): an
    override set during processing or before a reprocess is picked up.
    """
    recorded = document.sanitized_entity_types
    known = recorded is not None and document.sanitized_policy_digest is not None
    if document.ingestion_status != "completed":
        return DocumentStaleness(sanitized_policy_known=known)
    if current is None:
        raise ValueError("A completed document needs the current policy snapshot")
    if not known:
        return DocumentStaleness(needs_reprocessing=True, sanitized_policy_known=False)
    recorded_set = set(recorded)
    missing = [t for t in current.entity_types if t not in recorded_set]
    language_moved = document.sanitized_language != effective_masking_language(document)
    return DocumentStaleness(
        needs_reprocessing=(
            bool(missing)
            or document.sanitized_policy_digest != current.digest
            or language_moved
        ),
        missing_entity_types=missing,
    )


__all__ = [
    "DocumentStaleness",
    "PolicySnapshot",
    "build_policy_snapshot",
    "current_policy_entity_types",
    "current_policy_snapshot",
    "custom_recognizer_definition",
    "detection_settings",
    "non_pii_rule_definition",
    "document_staleness",
    "snapshot_entity_types",
]
