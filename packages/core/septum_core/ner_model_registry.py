from __future__ import annotations

"""
Language-aware HuggingFace NER model registry.

Thin wrapper around ``transformers.pipeline`` that:
- Maps detected document language codes to concrete NER model IDs.
- Lazily loads and caches pipelines per language.
- Selects the appropriate Torch device via :func:`get_device`.

``transformers`` and ``torch`` are part of the ``septum-core[transformers]``
extra; installing the base package without the extra gives you all the
regex / validator / composer machinery without the ~2 GB model dependency
footprint. Importing this module when the extra is missing will fail at
``from transformers import pipeline`` below, which is the intended
behaviour — detectors that rely on the NER layer must install the extra.
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

from .device import get_device


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

    # Suggested alternative models per language. The dashboard exposes
    # these as one-click presets next to the override input so users can
    # try a community alternative without hunting Hugging Face for the
    # exact ID. Keep entries to known, currently-hosted IDs.
    SUGGESTED_MODELS: Dict[str, list[Dict[str, str]]] = field(
        default_factory=lambda: {
            "tr": [
                {
                    "model_id": "akdeniz27/xlm-roberta-base-turkish-ner",
                    "label": "akdeniz27 XLM-RoBERTa (default)",
                    "description": "F1=0.949 on WikiANN-tr; current Septum default.",
                },
                {
                    "model_id": "savasy/bert-base-turkish-ner-cased",
                    "label": "savasy BERT-base",
                    "description": "BERT-base cased variant; lower memory footprint.",
                },
                {
                    "model_id": "akdeniz27/convbert-base-turkish-cased-ner",
                    "label": "akdeniz27 ConvBERT",
                    "description": "ConvBERT alternative; faster inference at similar F1.",
                },
            ],
            "en": [
                {
                    "model_id": "Davlan/xlm-roberta-base-wikiann-ner",
                    "label": "Davlan XLM-RoBERTa WikiANN (default)",
                    "description": "Multilingual, 20+ languages.",
                },
                {
                    "model_id": "dslim/bert-base-NER",
                    "label": "dslim BERT-base NER",
                    "description": "English-only; smaller, faster.",
                },
            ],
        }
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
