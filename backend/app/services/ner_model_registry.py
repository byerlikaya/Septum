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

    The registry is intentionally lightweight and in-memory only.
    Persistence of user overrides (language → model ID) can be added
    later without changing the public interface.
    """

    _loaded_models: Dict[str, object] = field(default_factory=dict)

    # Default language → model mapping. Keys are ISO 639-1 language codes.
    DEFAULT_MODEL_MAP: Dict[str, str] = field(
        default_factory=lambda: {
            "en": "dslim/bert-base-NER",
            "tr": "savasy/bert-base-turkish-ner-cased",
            "de": "dbmdz/bert-large-cased-finetuned-conll03-german",
            "fr": "Jean-Baptiste/roberta-large-ner-english",
            "es": "mrm8488/bert-spanish-cased-finetuned-ner",
            "nl": "wietsedv/bert-base-dutch-cased-finetuned-ner",
            "zh": "uer/roberta-base-finetuned-cluener2020-chinese",
            "ar": "hatmimoha/arabic-ner-bert",
            "ru": "DeepPavlov/rubert-base-cased-ner",
            "pt": "malduwais/biobert-base-cased-v1.2-finetuned-ner",
            "ja": "cl-tohoku/bert-base-japanese",
            "fallback": "Babelscape/wikineural-multilingual-ner",
        }
    )

    def get_pipeline(self, language: str) -> object:
        """Return a cached NER pipeline for the given language."""
        lang = (language or "en").lower()
        if lang not in self._loaded_models:
            model_name = self._get_model_name(lang)
            device = get_device()
            # transformers expects -1 for CPU, integer index otherwise.
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

        For now this method only uses the in-memory default mapping.
        A future iteration can incorporate user-configurable overrides
        from the database without changing callers.
        """
        if language in self.DEFAULT_MODEL_MAP:
            return self.DEFAULT_MODEL_MAP[language]
        return self.DEFAULT_MODEL_MAP["fallback"]

