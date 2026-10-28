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

import logging
import threading
from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional

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
from .language_resolution import canonical_language_code
from .model_integrity import ensure_model_snapshot, repo_id_of
from .model_pins import OPT_IN_MODELS
from .ner_windowing import (
    NERWindowingError,
    Span,
    Window,
    merge_window_spans,
    plan_windows,
)

logger = logging.getLogger(__name__)

_ERNIE_CLUENER_MODEL_ID = "gyr66/Ernie-3.0-base-chinese-finetuned-ner"
_DAVLAN_MBERT_NER_MODEL_ID = "Davlan/bert-base-multilingual-cased-ner-hrl"
_DAVLAN_XLMR_WIKIANN_MODEL_ID = "Davlan/xlm-roberta-base-wikiann-ner"

# Model-native entity labels mapped to the CoNLL tags the detector reads, or
# to an entity tag ``_map_ner_label`` accepts (EMAIL). Only listed labels are
# kept. A model absent from this table already emits CoNLL tags and runs
# unchanged. Keyed by Hub repo id, so a pinned ``org/name@<sha>`` spec matches.
MODEL_LABEL_MAPS: Dict[str, Dict[str, str]] = {
    # gyr66 Ernie-3.0 CLUENER labels. QQ, vx, mobile, position, game, book,
    # movie and scene have no detector entity here and are dropped; Presidio
    # covers phone numbers.
    _ERNIE_CLUENER_MODEL_ID: {
        "name": "PER",
        "address": "LOC",
        "company": "ORG",
        "organization": "ORG",
        "government": "ORG",
        "email": "EMAIL",
    },
}


def _preset_label(model_id: str, name: str) -> str:
    """Dashboard label of a preset; an opt-in model carries its stored reason."""
    if model_id not in OPT_IN_MODELS:
        return name
    reason = OPT_IN_MODELS[model_id]
    return f"{name} (opt-in: {reason})" if reason else f"{name} (opt-in)"


# Windows per pipeline call.
_NER_BATCH_SIZE = 8

# Re-tokenizing a window's text can add a few subword pieces at its edges.
# Windows are first planned this many tokens under the budget; a window that
# still overflows shrinks the plan by its excess and is planned again.
_RETOKENIZE_MARGIN = 4

# Planning passes per document before giving up. Each overflow shrinks the
# limit by more than the last, so a failing input costs a bounded number of
# tokenizations.
_MAX_FIT_PASSES = 16

# Tokenizers report this (or more) as ``model_max_length`` when none is set.
_UNSET_MAX_LENGTH = 10**12

# Labels Septum deliberately does not treat as entities. Any other model label
# that no mapping recognises is reported once when the model loads.
_IGNORED_NER_LABELS = frozenset({"O", "DATE", "MISC"})


class _WindowedNERPipeline:
    """Run a token-classification pipeline over a text of any length.

    The model sees at most its token budget, and the pipeline truncates
    anything longer, silently. The text is cut into overlapping windows of
    whole tokens, scored in one batched call, and merged back into document
    coordinates (see :mod:`septum_core.ner_windowing`). A text that fits in
    one window is scored whole, since its own tokenization fits the budget.
    The pipeline's own stride is not used: some models leave ``model_max_length``
    unset, which bypasses it; overflow fails closed on re-tokenization drift;
    and split names are joined only across intra-line spaces.
    """

    def __init__(self, pipe: object, label_map: Optional[Mapping[str, str]]) -> None:
        self._pipe = pipe
        self._label_map = label_map
        self._budget = _content_budget(pipe)
        if self._budget < _RETOKENIZE_MARGIN + 2:
            raise NERWindowingError(f"token budget of {self._budget} is too small for NER windows")

    def __call__(self, text: str) -> list[dict]:
        if not text:
            return []
        offsets = self._token_offsets(text)
        if len(offsets) <= self._budget:
            windows = [Window(0, len(text), True, True)]
            chunks = [text]
        else:
            windows, chunks = self._fit_windows(text, offsets)
        raw_per_window = self._pipe(chunks, batch_size=_NER_BATCH_SIZE)
        if len(raw_per_window) != len(windows):
            raise NERWindowingError(
                f"pipeline returned {len(raw_per_window)} result lists for {len(windows)} windows"
            )
        per_window = [
            self._to_spans(w.char_start, raw) for w, raw in zip(windows, raw_per_window)
        ]
        return [
            {
                "entity_group": span.label,
                "score": span.score,
                "start": span.start,
                "end": span.end,
            }
            for span in merge_window_spans(windows, per_window, text)
        ]

    def unmapped_labels(self) -> list[str]:
        """Model labels that neither the detector nor the label map recognises.

        A model with a label map is judged by that map alone: the labels it
        leaves out are dropped on purpose. Returns label names only, never
        document text, sorted and without the B-/I- prefix.
        """
        from .detector import Detector

        if self._label_map is not None:
            return []
        unmapped: set[str] = set()
        for label in self._pipe.model.config.id2label.values():
            base = label[2:] if label[:2] in ("B-", "I-") and len(label) > 2 else label
            if base in _IGNORED_NER_LABELS:
                continue
            if Detector._map_ner_label(label) is not None:
                continue
            unmapped.add(base)
        return sorted(unmapped)

    def _token_offsets(self, text: str) -> list[tuple[int, int]]:
        encoding = self._pipe.tokenizer(
            text, add_special_tokens=False, return_offsets_mapping=True
        )
        return [(int(a), int(b)) for a, b in encoding["offset_mapping"]]

    def _fit_windows(
        self, text: str, offsets: list[tuple[int, int]]
    ) -> tuple[list[Window], list[str]]:
        """Plan windows whose re-tokenized text fits the budget, shrinking the plan on overflow."""
        budget = self._budget
        limit = budget - _RETOKENIZE_MARGIN
        shift = 0
        passes = 0
        while limit >= 2 and passes < _MAX_FIT_PASSES:
            passes += 1
            windows = plan_windows(offsets, len(text), limit)
            chunks = [text[w.char_start:w.char_end] for w in windows]
            excess = max(self._token_counts(chunks)) - budget
            if excess <= 0:
                return windows, chunks
            limit -= excess << shift
            shift += 1
        raise NERWindowingError(f"NER windows cannot be fitted to the {budget}-token budget")

    def _token_counts(self, chunks: list[str]) -> list[int]:
        encodings = self._pipe.tokenizer(chunks, add_special_tokens=False)["input_ids"]
        return [len(ids) for ids in encodings]

    def _to_spans(self, offset: int, raw: list[dict]) -> list[Span]:
        spans: list[Span] = []
        for item in raw:
            label = item.get("entity_group") or item.get("entity")
            if self._label_map is not None:
                label = self._label_map.get(label or "")
            if not label:
                continue
            spans.append(
                Span(
                    start=offset + int(item["start"]),
                    end=offset + int(item["end"]),
                    label=label,
                    score=float(item.get("score", 0.0)),
                )
            )
        return spans


def _content_budget(pipe: object) -> int:
    """Content tokens one pipeline call accepts, special tokens excluded.

    The pipeline truncates at the tokenizer's ``model_max_length``, and the
    model has its own position limit. The smaller bound applies to the whole
    input, [CLS] and [SEP] included. A tokenizer with no ``model_max_length``
    defers to the position limit, which the model must then provide.
    """
    tokenizer = pipe.tokenizer
    limit = int(tokenizer.model_max_length)
    positions = _usable_positions(pipe.model)
    if positions is not None:
        limit = min(limit, positions)
    if limit >= _UNSET_MAX_LENGTH:
        raise NERWindowingError(
            "cannot derive the NER token limit: the tokenizer sets no model_max_length "
            "and the model exposes no position embeddings"
        )
    return limit - int(tokenizer.num_special_tokens_to_add(False))


def _usable_positions(model: object) -> int | None:
    """Positions a token can occupy, read from the model's position embeddings.

    Architectures whose position ids start after the padding id lose
    ``padding_idx + 1`` positions. ``None`` when the model exposes no position
    embeddings.
    """
    base = getattr(model, "base_model", model)
    embeddings = getattr(getattr(base, "embeddings", None), "position_embeddings", None)
    if embeddings is None:
        return None
    padding = getattr(embeddings, "padding_idx", None)
    offset = 0 if padding is None else int(padding) + 1
    return int(embeddings.num_embeddings) - offset


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
            "zh": [
                {
                    "model_id": _DAVLAN_MBERT_NER_MODEL_ID,
                    "label": "Davlan mBERT NER HRL (default)",
                    "description": "Multilingual; zh is one of its ten training languages. AFL-3.0.",
                },
                {
                    "model_id": _DAVLAN_XLMR_WIKIANN_MODEL_ID,
                    "label": "Davlan XLM-RoBERTa WikiANN",
                    "description": "Scores higher than the default on the public WikiANN zh set, but leaves more names and places unmasked on Septum's own Chinese test documents.",
                },
                {
                    "model_id": _ERNIE_CLUENER_MODEL_ID,
                    "label": _preset_label(_ERNIE_CLUENER_MODEL_ID, "gyr66 Ernie-3.0 CLUENER"),
                    "description": "Opt-in only. The Hub page declares no licence; confirm usage rights before enabling.",
                },
            ],
            "en": [
                {
                    "model_id": _DAVLAN_XLMR_WIKIANN_MODEL_ID,
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

    # Per-language ensemble: every model in the list runs on the chunk and
    # their spans are unioned through the regular dedup pipeline. Multiple
    # models per language give more recall (a name one model misses another
    # often catches), at the cost of (N × inference time + N × ~500 MB
    # model memory). Defaults stay single-model except for Turkish where
    # the second BERT-cased model complements the XLM-RoBERTa one on rare
    # surnames.
    DEFAULT_MODEL_MAP: Dict[str, list[str]] = field(
        default_factory=lambda: {
            # akdeniz27 XLM-RoBERTa F1=0.949 + savasy BERT-cased — different
            # architectures catch different names; ensemble lifts recall
            # on rare surnames the XLM-RoBERTa encoder underweights.
            "tr": [
                "akdeniz27/xlm-roberta-base-turkish-ner",
                "savasy/bert-base-turkish-ner-cased",
            ],
            # Davlan/xlm-roberta-base-wikiann-ner: Multilingual XLM-RoBERTa
            # supporting ar, as, bn, ca, en, es, eu, fr, gu, hi, id, ig, mr,
            # pa, pt, sw, ur, vi, yo, zh. It is also the only model here with
            # any Thai recall, which is why th uses it.
            "en": [_DAVLAN_XLMR_WIKIANN_MODEL_ID],
            "ar": [_DAVLAN_XLMR_WIKIANN_MODEL_ID],
            "es": [_DAVLAN_XLMR_WIKIANN_MODEL_ID],
            "fr": [_DAVLAN_XLMR_WIKIANN_MODEL_ID],
            "pt": [_DAVLAN_XLMR_WIKIANN_MODEL_ID],
            "hi": [_DAVLAN_XLMR_WIKIANN_MODEL_ID],
            "bn": [_DAVLAN_XLMR_WIKIANN_MODEL_ID],
            "ur": [_DAVLAN_XLMR_WIKIANN_MODEL_ID],
            "vi": [_DAVLAN_XLMR_WIKIANN_MODEL_ID],
            # hrl is the zh default and general fallback; it leaks less than the
            # wikiann model on Septum's own zh documents.
            "zh": [_DAVLAN_MBERT_NER_MODEL_ID],
            "ja": [_DAVLAN_MBERT_NER_MODEL_ID],
            "de": [_DAVLAN_MBERT_NER_MODEL_ID],
            "it": [_DAVLAN_MBERT_NER_MODEL_ID],
            "nl": [_DAVLAN_MBERT_NER_MODEL_ID],
            "pl": [_DAVLAN_MBERT_NER_MODEL_ID],
            "ru": [_DAVLAN_MBERT_NER_MODEL_ID],
            "ko": [_DAVLAN_MBERT_NER_MODEL_ID],
            "th": [_DAVLAN_XLMR_WIKIANN_MODEL_ID],
            "fallback": [_DAVLAN_MBERT_NER_MODEL_ID],
        }
    )

    def get_pipelines(self, language: str) -> list[object]:
        """Return cached NER pipelines for the given language.

        Returns one or more pipelines (the language ensemble). Pipelines
        are cached by *model id*, not by language, so a model used by
        many languages (e.g. wikiann across 11 locales) is only loaded
        once. Thread-safe: the actual ``pipeline(...)`` initialization
        is guarded by ``_load_lock``.

        Every model is loaded from a pinned, hash-verified local snapshot
        (safetensors only, no remote code). Raises
        :class:`~septum_core.model_integrity.UnpinnedModelError` or
        :class:`~septum_core.model_integrity.ModelIntegrityError` instead of
        loading anything unverified.
        """
        lang = canonical_language_code(language) or "en"
        model_ids = self._resolve_model_ids(lang)

        # Fast path — every model already loaded.
        loaded = [self._loaded_models.get(mid) for mid in model_ids]
        if all(p is not None for p in loaded):
            return loaded  # type: ignore[return-value]

        # Slow path — load whichever models are missing.
        device = get_device()
        device_index = -1 if device == "cpu" else 0
        with self._load_lock:
            for mid in model_ids:
                if mid in self._loaded_models:
                    continue
                model_dir = str(ensure_model_snapshot(mid))
                loaded_pipe = _WindowedNERPipeline(
                    pipeline(
                        "ner",
                        model=model_dir,
                        tokenizer=model_dir,
                        aggregation_strategy="simple",
                        device=device_index,
                        trust_remote_code=False,
                        model_kwargs={"use_safetensors": True},
                    ),
                    MODEL_LABEL_MAPS.get(repo_id_of(mid)),
                )
                unmapped = loaded_pipe.unmapped_labels()
                if unmapped:
                    logger.warning(
                        "NER model %s emits labels with no entity mapping, ignored: %s",
                        repo_id_of(mid),
                        ", ".join(unmapped),
                    )
                self._loaded_models[mid] = loaded_pipe
        return [self._loaded_models[mid] for mid in model_ids]

    def get_pipeline(self, language: str) -> object:
        """Return the first NER pipeline for ``language`` (legacy entry-point).

        Kept so existing single-pipeline call sites keep working;
        new code should call :meth:`get_pipelines` to opt into the
        per-language ensemble.
        """
        return self.get_pipelines(language)[0]

    def _resolve_model_ids(self, language: str) -> list[str]:
        """Resolve the active model id list for a language.

        Override format is comma-separated so a single string field on
        ``AppSettings.ner_model_overrides`` can carry either one model
        id (current behaviour) or several to opt into an ensemble. Each id
        is a manifest model or ``org/name@<40-hex commit>``.
        """
        language = canonical_language_code(language)
        override = (self._overrides.get(language) or "").strip()
        if override:
            ids = [m.strip() for m in override.split(",") if m.strip()]
            if ids:
                return ids
        if language in self.DEFAULT_MODEL_MAP:
            return list(self.DEFAULT_MODEL_MAP[language])
        return list(self.DEFAULT_MODEL_MAP["fallback"])


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
