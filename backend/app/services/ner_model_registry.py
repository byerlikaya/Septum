from __future__ import annotations

"""
Language-aware HuggingFace NER model registry.

This module provides a thin wrapper around `transformers.pipeline` which:
- Maps detected document language codes to concrete NER model IDs.
- Lazily loads and caches pipelines per language.
- Selects the appropriate Torch device via :func:`get_device`.

The goal is to centralize model loading logic so the sanitizer can
remain focused on PII detection and anonymization semantics.
"""

import threading
from dataclasses import dataclass, field
from typing import Dict

# Eagerly import the transformers symbol at module load time. The
# transformers package uses a ``_LazyModule`` for top-level attribute
# access, and concurrent ``from transformers import pipeline`` calls from
# multiple ingestion worker threads were observed to race the lazy
# loader: one thread would see ``transformers.pipeline`` as a not-yet-
# materialized attribute and raise ``ImportError: cannot import name
# 'pipeline' from 'transformers'``. Pulling the import to module scope
# happens exactly once on first import (under the GIL) and removes the
# race entirely. Don't move this back inside ``get_pipeline``.
from transformers import pipeline  # noqa: E402

from ..utils.device import get_device


@dataclass
class NERModelRegistry:
    """Registry responsible for providing NER pipelines per language.

    User overrides (language → model ID) can be passed in and are
    consulted before the default mapping.
    """

    _loaded_models: Dict[str, object] = field(default_factory=dict)
    _overrides: Dict[str, str] = field(default_factory=dict)
    # Guards the actual ``pipeline(...)`` call inside ``get_pipeline``.
    # Without this, two concurrent ingestion workers asking for the same
    # language would both pass the ``if lang not in self._loaded_models``
    # check and both download + initialize the same HuggingFace model,
    # wasting memory and racing on the cache files. Double-checked
    # locking pattern: fast path takes no lock, slow path takes the lock
    # and re-checks before committing to a load.
    _load_lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    DEFAULT_MODEL_MAP: Dict[str, str] = field(
        default_factory=lambda: {
            # Best-in-class models per language based on 2024-2025 research
            # akdeniz27/xlm-roberta-base-turkish-ner: F1=0.949, XLM-RoBERTa fine-tuned for tr locale
            "tr": "akdeniz27/xlm-roberta-base-turkish-ner",
            # Davlan/xlm-roberta-base-wikiann-ner: Multilingual XLM-RoBERTa for 20 languages
            # Supports: ar, as, bn, ca, en, es, eu, fr, gu, hi, id, ig, mr, pa, pt, sw, ur, vi, yo, zh
            "en": "Davlan/xlm-roberta-base-wikiann-ner",
            "ar": "Davlan/xlm-roberta-base-wikiann-ner",
            "zh": "Davlan/xlm-roberta-base-wikiann-ner",
            "es": "Davlan/xlm-roberta-base-wikiann-ner",
            "fr": "Davlan/xlm-roberta-base-wikiann-ner",
            "pt": "Davlan/xlm-roberta-base-wikiann-ner",
            "hi": "Davlan/xlm-roberta-base-wikiann-ner",
            "bn": "Davlan/xlm-roberta-base-wikiann-ner",
            "ur": "Davlan/xlm-roberta-base-wikiann-ner",
            "vi": "Davlan/xlm-roberta-base-wikiann-ner",
            # Babelscape/wikineural-multilingual-ner: EMNLP 2021, supports 9 European languages
            # Supports: de, en, es, fr, it, nl, pl, pt, ru
            "de": "Babelscape/wikineural-multilingual-ner",
            "it": "Babelscape/wikineural-multilingual-ner",
            "nl": "Babelscape/wikineural-multilingual-ner",
            "pl": "Babelscape/wikineural-multilingual-ner",
            "ru": "Babelscape/wikineural-multilingual-ner",
            # ja is covered by the Davlan multilingual WikiANN model
            "ja": "Davlan/xlm-roberta-base-wikiann-ner",
            # Fallback: Babelscape for best multilingual coverage
            "fallback": "Babelscape/wikineural-multilingual-ner",
        }
    )

    def get_pipeline(self, language: str) -> object:
        """Return a cached NER pipeline for the given language.

        Thread-safe: the actual ``pipeline(...)`` initialization is
        guarded by ``_load_lock`` so concurrent ingestion tasks for the
        same language do not redundantly download or load the model.
        The fast path (already-loaded model) is lock-free.
        """
        lang = (language or "en").lower()
        cached = self._loaded_models.get(lang)
        if cached is not None:
            return cached
        with self._load_lock:
            cached = self._loaded_models.get(lang)
            if cached is not None:
                return cached
            model_name = self._get_model_name(lang)
            device = get_device()
            device_index = -1 if device == "cpu" else 0
            self._loaded_models[lang] = pipeline(
                "ner",
                model=model_name,
                aggregation_strategy="simple",
                device=device_index,
            )
            return self._loaded_models[lang]

    def _get_model_name(self, language: str) -> str:
        """Resolve the model name for a language.

        User overrides take precedence; then the default mapping is used.
        """
        if language in self._overrides and (self._overrides[language] or "").strip():
            return (self._overrides[language] or "").strip()
        if language in self.DEFAULT_MODEL_MAP:
            return self.DEFAULT_MODEL_MAP[language]
        return self.DEFAULT_MODEL_MAP["fallback"]


_shared_registry: NERModelRegistry | None = None
_registry_lock = threading.Lock()


def get_shared_ner_registry(
    overrides: Dict[str, str] | None = None,
) -> NERModelRegistry:
    """Return a process-wide NERModelRegistry singleton.

    Loaded NER pipelines (~500 MB each) survive across requests so the
    same model is never loaded twice for the same language.
    """
    global _shared_registry
    if _shared_registry is not None:
        return _shared_registry
    with _registry_lock:
        if _shared_registry is not None:
            return _shared_registry
        _shared_registry = NERModelRegistry(_overrides=overrides or {})
        return _shared_registry

