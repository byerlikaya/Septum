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

from dataclasses import dataclass, field
from typing import Dict

from transformers import pipeline

from ..utils.device import get_device


@dataclass
class NERModelRegistry:
    """Registry responsible for providing NER pipelines per language.

    User overrides (language → model ID) can be passed in and are
    consulted before the default mapping.
    """

    _loaded_models: Dict[str, object] = field(default_factory=dict)
    _overrides: Dict[str, str] = field(default_factory=dict)

    DEFAULT_MODEL_MAP: Dict[str, str] = field(
        default_factory=lambda: {
            # Best-in-class models per language based on 2024-2025 research
            # akdeniz27/xlm-roberta-base-turkish-ner: F1=0.949, XLM-RoBERTa fine-tuned on Turkish NER
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
            # Japanese: Keep existing specialized model
            "ja": "cl-tohoku/bert-base-japanese",
            # Fallback: Babelscape for best multilingual coverage
            "fallback": "Babelscape/wikineural-multilingual-ner",
        }
    )

    def get_pipeline(self, language: str) -> object:
        """Return a cached NER pipeline for the given language."""
        lang = (language or "en").lower()
        if lang not in self._loaded_models:
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

