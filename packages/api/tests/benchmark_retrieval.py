"""Retrieval benchmark for Septum.

Exercises the shipped retrieval stack: the production whole-document path
(``DocumentPipeline`` block normalization, one detection over the whole text,
placeholders in document order, protected ranges, ``_chunk_records`` with the
character settings, per-chunk projection), ``VectorStore`` (FAISS) and
``BM25Retriever`` for indexing the raw chunk slices, and
``VectorStore.hybrid_search`` for ranking. The index lives under ``tmp_path`` and the encryption key is
generated per test; the real data directory and bootstrap config are never
touched. Everything runs offline: the embedder is the pinned, hash-verified
snapshot in the local Hugging Face cache, and no LLM is called.

The smoke test (``-k smoke``) checks the embedder pin and one hybrid query.
The full run (``-k full_retrieval``) builds the hand-authored evaluation set
(``retrieval_eval/``), measures quality per retriever and query mode, records
the leak block, and writes one JSON file when ``SEPTUM_BENCHMARK_OUT`` is set.
The full run is also a gate (``gate_failures``): leak and quality-guard limits.

Environment:
  SEPTUM_BENCHMARK_EMBEDDING_MODEL  embedding model id (default: the production MiniLM)
  SEPTUM_BENCHMARK_CHUNKER          pipeline | structured | sliding (default pipeline)
  SEPTUM_BENCHMARK_CHUNK_OVERLAP    chunk overlap in characters, overriding the 200 default (optional)
  SEPTUM_BENCHMARK_RETRIEVERS       comma list of dense,bm25,hybrid,auto_rag
  SEPTUM_BENCHMARK_QUERY_MODE       raw | sanitized | both (default both; sanitized is the headline)
  SEPTUM_BENCHMARK_LATENCY_REPEATS  timed repetitions per query (default 5)
  SEPTUM_BENCHMARK_OUT              path of the JSON result file (optional)

Not collected by default (the file name does not match ``test_*``).

Run the smoke test:  HF_HUB_OFFLINE=1 pytest tests/benchmark_retrieval.py -k smoke -s -q
Run the full study:  HF_HUB_OFFLINE=1 pytest tests/benchmark_retrieval.py -k full_retrieval -s -q
"""

from __future__ import annotations

import asyncio
import bisect
import copy
import hashlib
import json
import os
import platform
import random
import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Awaitable, Callable, Optional, Sequence

import pytest
import torch
from langdetect import DetectorFactory
from septum_core.span_projection import project, protected_ranges
from septum_core.model_integrity import (
    ModelIntegrityError,
    ensure_pinned_snapshot,
    require_pin,
)

from septum_api.models.settings import AppSettings
from septum_api.request_limits import MAX_CONTEXT_CHUNKS
from septum_api.routers.chat import _DEFAULT_RELEVANCE_THRESHOLD
from septum_api.seeds.regulations import builtin_regulations
from septum_api.services.anonymization_map import AnonymizationMap
from septum_api.services.bm25_retriever import BM25Retriever
from septum_api.services.document_pipeline import DocumentPipeline, _normalized_blocks
from septum_api.services.ingestion.base import TextBlock, block_starts, join_blocks
from septum_api.services.ner_model_registry import get_shared_ner_registry
from septum_api.services.policy_composer import PolicyComposer
from septum_api.services.sanitizer import PIISanitizer
from septum_api.services.sanitizer_factory import DOCUMENT_SANITIZER_ENABLE_OLLAMA
from septum_api.services.vector_store import (
    _DEFAULT_ST_MODEL,
    VectorStore,
    get_shared_sentence_transformer,
)
from septum_api.utils import crypto
from septum_api.utils.db_helpers import detect_language
from tests import benchmark_detection as bd
from tests.retrieval_eval import (
    EVAL_DIR,
    SLOT_PATTERN,
    EvalDoc,
    EvalQuery,
    MixedDoc,
    fixture_digest,
    load_docs,
    load_mixed_docs,
    load_queries,
    mixed_fixture_digest,
    resolve_gold_spans,
    split_segments,
)
from tests.retrieval_metrics import (
    Opcode,
    alignment,
    bootstrap_ci,
    classify_boundary,
    fragment_scorable,
    has_fragment_leak,
    has_literal_leak,
    mean,
    mrr,
    ndcg_at_k,
    percentile,
    recall_at_k,
    sanitized_span,
)
from tests.retrieval_report import SCHEMA_VERSION

EMBEDDING_MODEL = os.environ.get("SEPTUM_BENCHMARK_EMBEDDING_MODEL", _DEFAULT_ST_MODEL)

SEED = 42
TOP_K = 10
BOOTSTRAP_RESAMPLES = 1000
DEFAULT_LATENCY_REPEATS = 5
LANGS = ("en", "tr", "zh")
ANSWERABLE_TYPES = ("lexical", "paraphrase", "entity", "cross_lingual")
METRICS = ("recall@5", "recall@10", "mrr", "ndcg@10")
UNRELATED: tuple[int, int] = (-1, -1)
POOL_SIZE = 120
LEAK_DOC_VALUES = 2400
LEAK_MIN_CROSSING = 50
LEAK_MAX_DOCS = 12
LEAK_VIEW_PAD = 60
LEAK_GAP_CHARS = (120, 180)
MIXED_LEAK_DOC_VALUES = 400
MIXED_LEAK_ROUNDS = 3
DEFAULT_CHUNK_OVERLAP = 200
GATE_MIN_RECALL_AT_5 = 0.951
GATE_MIN_MRR = 0.709
GATE_ZERO_LITERAL_TYPES = ("EMAIL_ADDRESS", "PHONE_NUMBER", "IBAN")
CHUNKER_DESCRIPTIONS = {
    "pipeline": (
        "whole-document detection, then the production strategy per format "
        "(txt/pdf structured, chunk_size or max(pdf_chunk_size, 800) characters, "
        "chunk_overlap) and per-chunk projection"
    ),
    "structured": "whole-document detection, structured chunks (chunk_size) for every document",
    "sliding": "whole-document detection, sliding chunks (chunk_size, chunk_overlap) for every document",
}
CHUNKER_FORMATS = {"pipeline": None, "structured": "txt", "sliding": "text"}
"""File format the production chunk strategy is looked up with; ``None`` is the document's own."""
RETRIEVER_NAMES = ("dense", "bm25", "hybrid", "auto_rag")
QUERY_MODE_CHOICES = ("raw", "sanitized", "both")
IDENTIFIER_TYPES = frozenset({"EMAIL_ADDRESS", "PHONE_NUMBER", "IBAN"})
LEAK_TYPES = ("PERSON_NAME", "LOCATION", "ORGANIZATION_NAME", "EMAIL_ADDRESS", "PHONE_NUMBER", "IBAN")
# Masked under the all-regulations policy, but detected by NER alone (no pattern recognizer
# backs it), so the leak block is also reported without it.
SEPARATED_LEAK_TYPE = "ORGANIZATION_NAME"
# Invented, synthetic organization names. Do not change them without re-recording the committed baseline.
ZH_ORGANIZATION_NAMES = (
    "北辰科技有限公司", "云帆信息技术有限公司", "海岳物流集团", "青松医疗器械股份有限公司",
    "金穗农业发展有限公司", "星河教育咨询有限公司", "远景能源科技有限公司", "明泽建材贸易有限公司",
)

Span = tuple[int, int]


def _make_settings(chunk_size: int = 800, chunk_overlap: int = 200, use_ner: bool = True) -> AppSettings:
    return AppSettings(
        id=1, llm_provider="unused", llm_model="unused",
        ollama_base_url="http://localhost:11434",
        ollama_chat_model="unused", ollama_deanon_model="unused",
        deanon_enabled=True, deanon_strategy="simple",
        require_approval=False, show_json_output=False,
        use_presidio_layer=True, use_ner_layer=use_ner,
        use_ollama_layer=False, use_ollama_validation_layer=False,
        use_ollama_semantic_layer=False,
        chunk_size=chunk_size, chunk_overlap=chunk_overlap, top_k_retrieval=TOP_K,
        pdf_chunk_size=1200, audio_chunk_size=60, spreadsheet_chunk_size=200,
        whisper_model="base", image_ocr_languages=["en"],
        ocr_provider="paddleocr", ocr_provider_options=None,
        extract_embedded_images=True, recursive_email_attachments=True,
        default_active_regulations=["gdpr"],
    )


def _build_sanitizer(settings: AppSettings) -> PIISanitizer:
    """The ingestion sanitizer with every built-in regulation active.

    Mirrors ``sanitizer_factory.create_sanitizer`` with the policy composed
    from the seed data instead of the database.
    """
    regs = builtin_regulations()
    for reg in regs:
        reg.is_active = True
    policy = PolicyComposer().compose_from_data(regs, [], [])
    return PIISanitizer(
        settings=settings,
        policy=policy,
        ner_registry=get_shared_ner_registry(overrides=None),
        enable_ollama_layer=DOCUMENT_SANITIZER_ENABLE_OLLAMA,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class BenchmarkConfig:
    embedding_model: str
    chunker: str
    retrievers: tuple[str, ...]
    query_modes: tuple[str, ...]
    latency_repeats: int
    out: Optional[Path]
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP

    @property
    def headline_mode(self) -> str:
        return "sanitized" if "sanitized" in self.query_modes else self.query_modes[0]


def load_config() -> BenchmarkConfig:
    chunker = os.environ.get("SEPTUM_BENCHMARK_CHUNKER", "pipeline")
    if chunker not in CHUNKER_DESCRIPTIONS:
        raise ValueError(f"SEPTUM_BENCHMARK_CHUNKER must be one of {sorted(CHUNKER_DESCRIPTIONS)}")
    retrievers = tuple(
        name.strip()
        for name in os.environ.get("SEPTUM_BENCHMARK_RETRIEVERS", ",".join(RETRIEVER_NAMES)).split(",")
        if name.strip()
    )
    unknown = set(retrievers) - set(RETRIEVER_NAMES)
    if unknown or not retrievers:
        raise ValueError(f"SEPTUM_BENCHMARK_RETRIEVERS: unknown {sorted(unknown)}")
    query_mode = os.environ.get("SEPTUM_BENCHMARK_QUERY_MODE", "both")
    if query_mode not in QUERY_MODE_CHOICES:
        raise ValueError(f"SEPTUM_BENCHMARK_QUERY_MODE must be one of {QUERY_MODE_CHOICES}")
    modes = ("raw", "sanitized") if query_mode == "both" else (query_mode,)
    repeats = int(os.environ.get("SEPTUM_BENCHMARK_LATENCY_REPEATS", DEFAULT_LATENCY_REPEATS))
    if repeats < 1:
        raise ValueError("SEPTUM_BENCHMARK_LATENCY_REPEATS must be at least 1")
    overlap = int(os.environ.get("SEPTUM_BENCHMARK_CHUNK_OVERLAP", DEFAULT_CHUNK_OVERLAP))
    if overlap < 0:
        raise ValueError("SEPTUM_BENCHMARK_CHUNK_OVERLAP must not be negative")
    out = os.environ.get("SEPTUM_BENCHMARK_OUT")
    return BenchmarkConfig(
        embedding_model=EMBEDDING_MODEL,
        chunker=chunker,
        retrievers=retrievers,
        query_modes=modes,
        latency_repeats=repeats,
        out=Path(out) if out else None,
        chunk_overlap=overlap,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  SEEDED VALUES AND SLOT FILLING
# ═══════════════════════════════════════════════════════════════════════════

@lru_cache(maxsize=1)
def _pools() -> tuple[dict[str, tuple[str, ...]], dict[str, dict[str, tuple[str, ...]]]]:
    """Synthetic value pools from the detection benchmark's seeded generators.

    The generators draw from the benchmark module's private RNG; it is swapped
    for a fresh seeded one during the build so the pools never depend on what
    else ran in the process. This depends on ``benchmark_detection._RNG`` being
    the module attribute the generators read; renaming it there breaks this swap.
    """
    saved = bd._RNG
    bd._RNG = random.Random(SEED)
    try:
        language_free = {
            "EMAIL_ADDRESS": tuple(bd._generate_emails(POOL_SIZE)),
            "PHONE_NUMBER": tuple(bd._generate_phones(POOL_SIZE)),
            "IBAN": tuple(bd._generate_ibans(POOL_SIZE)),
            "DATE_OF_BIRTH": tuple(bd._generate_dates_of_birth(POOL_SIZE)),
        }
    finally:
        bd._RNG = saved
    per_language = {
        "PERSON_NAME": {
            "en": tuple(bd.PERSON_NAMES_EN), "tr": tuple(bd.PERSON_NAMES_TR), "zh": tuple(bd.PERSON_NAMES_ZH),
        },
        "LOCATION": {
            "en": tuple(bd.LOCATIONS_EN), "tr": tuple(bd.LOCATIONS_TR), "zh": tuple(bd.LOCATIONS_ZH),
        },
        "ORGANIZATION_NAME": {
            "en": tuple(bd.ORGANIZATION_NAMES), "tr": tuple(bd.ORGANIZATION_NAMES_TR),
            "zh": ZH_ORGANIZATION_NAMES,
        },
    }
    return language_free, per_language


def _pool(slot_type: str, lang: str) -> tuple[str, ...]:
    language_free, per_language = _pools()
    if slot_type in language_free:
        return language_free[slot_type]
    if slot_type in per_language and lang in per_language[slot_type]:
        return per_language[slot_type][lang]
    raise ValueError(f"no synthetic pool for slot {slot_type} in language {lang}")


def _pick(scope: str, lang: str, slot_type: str, index: int, salt: int = 0) -> str:
    digest = hashlib.sha256(f"{SEED}|{scope}|{slot_type}|{index}|{salt}".encode()).digest()
    pool = _pool(slot_type, lang)
    return pool[int.from_bytes(digest[:8], "big") % len(pool)]


def fill_document(doc_key: int, lang: str, template: str) -> tuple[str, dict[tuple[str, int], str]]:
    """Replace every slot with a seeded value; values are distinct within the document where the pool allows."""
    values: dict[tuple[str, int], str] = {}
    taken: set[str] = set()
    scope = f"doc{doc_key}"

    def _sub(match: re.Match[str]) -> str:
        key = (match.group(1), int(match.group(2)))
        if key not in values:
            salt = 0
            value = _pick(scope, lang, key[0], key[1], salt)
            while value in taken and salt < 50:
                salt += 1
                value = _pick(scope, lang, key[0], key[1], salt)
            values[key] = value
            taken.add(value)
        return values[key]

    return SLOT_PATTERN.sub(_sub, template), values


def fill_query(text: str, doc_key: Optional[int], lang: str, values: dict[tuple[str, int], str]) -> str:
    """Fill a query's slots with its document's values (same seeded rule for slots the document lacks)."""
    if doc_key is None and SLOT_PATTERN.search(text):
        raise ValueError("a query without a document must not contain slots")

    def _sub(match: re.Match[str]) -> str:
        key = (match.group(1), int(match.group(2)))
        if key in values:
            return values[key]
        return _pick(f"doc{doc_key}", lang, key[0], key[1])

    return SLOT_PATTERN.sub(_sub, text)


# ═══════════════════════════════════════════════════════════════════════════
#  CHUNK OFFSETS
# ═══════════════════════════════════════════════════════════════════════════


def _locate_whitespace_normalised(source: str, body: str, cursor: int) -> Optional[Span]:
    """Match ``body`` against ``source[cursor:]`` with whitespace runs collapsed to one space.

    Returns the span in original ``source`` offsets, or None.
    """
    needle = " ".join(body.split())
    if not needle:
        return None
    chars: list[str] = []
    offsets: list[int] = []
    in_gap = False
    for index in range(cursor, len(source)):
        char = source[index]
        if char.isspace():
            if in_gap or not chars:
                continue
            in_gap = True
            chars.append(" ")
        else:
            in_gap = False
            chars.append(char)
        offsets.append(index)
    haystack = "".join(chars)
    at = haystack.find(needle)
    if at == -1:
        return None
    return offsets[at], offsets[at + len(needle) - 1] + 1


def locate_chunk(source: str, chunk: str, cursor: int) -> Optional[Span]:
    """Char span of ``chunk`` in ``source`` at or after ``cursor``, or None.

    Exact match first, then a whitespace-normalised match (a chunker that
    joins parts with a space where the source has a line break), then a chunk
    anchored on its first and last 30 characters.
    """
    body = chunk.strip()
    if not body:
        return None
    start = source.find(body, cursor)
    if start != -1:
        return start, start + len(body)
    span = _locate_whitespace_normalised(source, body, cursor)
    if span is not None:
        return span
    head, tail = body[:30], body[-30:]
    head_at = source.find(head, cursor)
    if head_at == -1:
        return None
    tail_at = source.find(tail, head_at)
    if tail_at == -1:
        return None
    return head_at, tail_at + len(tail)


def map_chunk_spans(source: str, chunk_texts: Sequence[str]) -> list[Optional[Span]]:
    """Map each chunk to its span in ``source``; the cursor is the previous chunk's start so overlaps resolve."""
    spans: list[Optional[Span]] = []
    cursor = 0
    for text in chunk_texts:
        span = locate_chunk(source, text, cursor)
        if span is not None:
            cursor = span[0]
        spans.append(span)
    return spans


# ═══════════════════════════════════════════════════════════════════════════
#  CHUNKER AND RETRIEVER REGISTRIES
# ═══════════════════════════════════════════════════════════════════════════

class _EmbedderTokens:
    """Counts tokens with the production embedder's tokenizer, only to measure chunks over its input limit."""

    def __init__(self) -> None:
        model = get_shared_sentence_transformer()
        self._tokenizer = model.tokenizer
        self.cap = int(model.max_seq_length) - self._tokenizer.num_special_tokens_to_add(pair=False)

    def count(self, text: str) -> int:
        return len(self._tokenizer(text, add_special_tokens=False, truncation=False)["input_ids"])


@dataclass(frozen=True)
class ChunkContext:
    """What the production path needs besides the document: pipeline and sanitizer."""

    pipeline: DocumentPipeline
    sanitizer: PIISanitizer


@dataclass(frozen=True)
class ChunkedDocument:
    """One document cut the production way: raw slices, their exact spans, their masked projections."""

    texts: list[str]
    spans: list[Span]
    sanitized: list[str]
    detect_s: float
    cut_s: float
    document_view: Callable[[Sequence[Span]], list[str]]
    """Masked text of arbitrary ranges of the document, as the whole-document view shows them."""


Chunker = Callable[[ChunkContext, str, str, str, AnonymizationMap], Awaitable[ChunkedDocument]]


def _document_blocks(text: str) -> tuple[list[TextBlock], str]:
    """One text block, NFC-normalized as ingestion does (no database rules); the joined text."""
    blocks = _normalized_blocks([TextBlock(text=text, kind="text")], lambda value: value)
    return blocks, join_blocks(blocks)


async def _chunk_document(
    ctx: ChunkContext, strategy_format: Optional[str], fmt: str, text: str, lang: str, anon_map: AnonymizationMap
) -> ChunkedDocument:
    """Mirror ``DocumentPipeline._mask_document`` without the database and model-scope plumbing.

    Detect once on the whole text, apply to ``anon_map`` (placeholders in document order),
    cut the chunks outside the protected ranges, project each chunk.
    """
    blocks, joined = _document_blocks(text)
    if joined != text:
        raise ValueError("block normalization changed the evaluation text; gold offsets would shift")
    started = time.perf_counter()
    detection = await asyncio.to_thread(ctx.sanitizer.detect, joined, lang, boundaries=block_starts(blocks))
    if detection.text != joined:
        raise ValueError("the detector normalized the evaluation text differently from its blocks")
    applied = await asyncio.to_thread(ctx.sanitizer.apply, detection, anon_map)
    detect_s = time.perf_counter() - started
    resolved = applied.detected_spans

    def cut_and_project():
        ranges = protected_ranges(detection, resolved, anon_map)
        records = ctx.pipeline._chunk_records(strategy_format or fmt, blocks, ranges)
        return records, project(detection, resolved, [(r.start, r.end) for r in records], anon_map)

    started = time.perf_counter()
    records, projected = await asyncio.to_thread(cut_and_project)
    cut_s = time.perf_counter() - started
    return ChunkedDocument(
        texts=[r.text for r in records],
        spans=[(r.start, r.end) for r in records],
        sanitized=[p.masked_text for p in projected],
        detect_s=detect_s,
        cut_s=cut_s,
        document_view=lambda ranges: [
            p.masked_text for p in project(detection, resolved, list(ranges), anon_map)
        ],
    )


def _chunker_for(name: str) -> Chunker:
    async def chunk(ctx: ChunkContext, fmt: str, text: str, lang: str, anon_map: AnonymizationMap) -> ChunkedDocument:
        return await _chunk_document(ctx, CHUNKER_FORMATS[name], fmt, text, lang, anon_map)

    return chunk


CHUNKERS: dict[str, Chunker] = {name: _chunker_for(name) for name in CHUNKER_DESCRIPTIONS}


@dataclass
class IndexedChunk:
    chunk_id: int
    doc_key: int
    text: str
    span: Optional[Span]


@dataclass
class Index:
    store: VectorStore
    bm25: BM25Retriever
    doc_keys: list[int]
    chunks: dict[int, IndexedChunk]


SearchFn = Callable[[Index, Optional[int], str], list[tuple[int, float]]]


def _dense_doc(index: Index, doc_key: Optional[int], text: str) -> list[tuple[int, float]]:
    return index.store.search(doc_key, text, TOP_K)


def _bm25_doc(index: Index, doc_key: Optional[int], text: str) -> list[tuple[int, float]]:
    return index.bm25.search(doc_key, text, TOP_K)


def _hybrid_doc(index: Index, doc_key: Optional[int], text: str) -> list[tuple[int, float]]:
    return index.store.hybrid_search(doc_key, text, TOP_K, bm25_retriever=index.bm25)


def _dense_all_documents(index: Index, doc_key: Optional[int], text: str) -> list[tuple[int, float]]:
    """Cross-document cosine search before the threshold; mirrors chat._retrieve_chunks_all_documents.

    The per-document candidate count and the descending sort follow that chat function, and
    tests/test_retrieval_auto_parity.py pins them against it. The per-document searches run
    serially here, whereas chat gathers them concurrently, so the measured latency is an upper bound.
    """
    per_doc_k = max(5, min(50 // max(len(index.doc_keys), 1), TOP_K))
    merged: list[tuple[int, float]] = []
    for key in index.doc_keys:
        merged.extend(index.store.search(key, text, top_k=per_doc_k))
    merged.sort(key=lambda pair: pair[1], reverse=True)
    return merged


@dataclass(frozen=True)
class Retriever:
    name: str
    scope: str  # "doc": the query's own document; "auto": every document
    search: SearchFn
    threshold: Optional[float] = None


RETRIEVERS: dict[str, Retriever] = {
    "dense": Retriever("dense", "doc", _dense_doc),
    "bm25": Retriever("bm25", "doc", _bm25_doc),
    "hybrid": Retriever("hybrid", "doc", _hybrid_doc),
    "auto_rag": Retriever("auto_rag", "auto", _dense_all_documents, threshold=_DEFAULT_RELEVANCE_THRESHOLD),
}


def _apply_threshold(candidates: list[tuple[int, float]], threshold: Optional[float]) -> list[tuple[int, float]]:
    if threshold is None:
        return candidates
    cap = min(TOP_K * 3, MAX_CONTEXT_CHUNKS)
    return [pair for pair in candidates if pair[1] >= threshold][:cap]


# ═══════════════════════════════════════════════════════════════════════════
#  EVALUATION
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class EvalDocument:
    key: int
    meta: EvalDoc
    filled: str
    values: dict[tuple[str, int], str]
    anon_map: AnonymizationMap
    chunk_ids: list[int] = field(default_factory=list)


@dataclass
class QueryCase:
    query: EvalQuery
    doc_key: Optional[int]
    lang: str
    gold: list[Span]
    variants: dict[tuple[str, str], str]  # (mode, scope) -> query text


def _spans_for_ranking(index: Index, ranking: list[tuple[int, float]], doc_key: int) -> list[Span]:
    spans: list[Span] = []
    for chunk_id, _ in ranking:
        chunk = index.chunks[chunk_id]
        if chunk.doc_key == doc_key and chunk.span is not None:
            spans.append(chunk.span)
        else:
            spans.append(UNRELATED)
    return spans


def _aggregate(rows: list[dict]) -> dict:
    result: dict = {"n": len(rows)}
    if not rows:
        return result
    for metric in METRICS:
        values = [row[metric] for row in rows]
        low, high = bootstrap_ci(values, seed=SEED, n_resamples=BOOTSTRAP_RESAMPLES)
        result[metric] = {"mean": mean(values), "ci95": [low, high]}
    return result


def _distribution(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean": mean(values),
        "p10": percentile(values, 10),
        "p50": percentile(values, 50),
        "p90": percentile(values, 90),
    }


def _evaluate_retriever(
    index: Index, retriever: Retriever, mode: str, cases: list[QueryCase]
) -> dict:
    rows: list[dict] = []
    negatives: list[dict] = []
    for case in cases:
        query = case.query
        text = case.variants[(mode, retriever.scope)]
        doc_key = case.doc_key if retriever.scope == "doc" else None
        candidates = retriever.search(index, doc_key, text)
        kept = _apply_threshold(candidates, retriever.threshold)
        top1 = candidates[0][1] if candidates else 0.0
        if query.doc_id is None:
            negatives.append({
                "lang": query.lang, "type": query.type,
                "false_retrieval": 1.0 if kept else 0.0, "top1": top1,
            })
            continue
        spans = _spans_for_ranking(index, kept[:TOP_K], case.doc_key)
        rows.append({
            "lang": query.lang, "type": query.type, "top1": top1,
            "recall@5": recall_at_k(spans, case.gold, 5),
            "recall@10": recall_at_k(spans, case.gold, TOP_K),
            "mrr": mrr(spans, case.gold),
            "ndcg@10": ndcg_at_k(spans, case.gold, TOP_K),
        })

    result: dict = {
        "overall": _aggregate(rows),
        "by_lang": {lang: _aggregate([r for r in rows if r["lang"] == lang]) for lang in LANGS},
        "by_type": {
            qtype: _aggregate([r for r in rows if r["type"] == qtype]) for qtype in ANSWERABLE_TYPES
        },
    }
    if retriever.threshold is not None:
        false_values = [n["false_retrieval"] for n in negatives]
        low, high = bootstrap_ci(false_values, seed=SEED, n_resamples=BOOTSTRAP_RESAMPLES) if false_values else (0.0, 0.0)
        result["negatives"] = {
            "n": len(negatives),
            "false_retrieval": {"mean": mean(false_values), "ci95": [low, high]},
            "score": {
                "positive": _distribution([r["top1"] for r in rows]),
                "negative": _distribution([n["top1"] for n in negatives]),
            },
        }
    return result


def _measure_latency(index: Index, retriever: Retriever, cases: list[QueryCase], mode: str, repeats: int) -> dict:
    items = [
        (case.doc_key if retriever.scope == "doc" else None, case.variants[(mode, retriever.scope)])
        for case in cases
        if retriever.scope == "auto" or case.doc_key is not None
    ]
    for doc_key, text in items:
        retriever.search(index, doc_key, text)
    samples: list[float] = []
    for _ in range(repeats):
        for doc_key, text in items:
            started = time.perf_counter()
            retriever.search(index, doc_key, text)
            samples.append((time.perf_counter() - started) * 1000)
    return {
        "p50_ms": percentile(samples, 50),
        "p95_ms": percentile(samples, 95),
        "samples": len(samples),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  LEAK BLOCK (record only)
# ═══════════════════════════════════════════════════════════════════════════


def _filler_text(lang: str) -> str:
    paragraphs: list[str] = []
    for path in sorted((EVAL_DIR / "corpus" / lang).glob("*.txt")):
        for block in path.read_text(encoding="utf-8").split("\n\n"):
            stripped = block.strip()
            if stripped and "{{" not in stripped:
                paragraphs.append(stripped)
    if not paragraphs:
        raise ValueError(f"no slot-free filler paragraphs for {lang}")
    return "\n\n".join(paragraphs)


LEAK_SPACE_WINDOW = 20
LEAK_PUNCT_WINDOW = 60


def _snap_to_boundary(text: str, end: int) -> int:
    """Move a cut forward to a word boundary so values never split a word.

    The next whitespace within ``LEAK_SPACE_WINDOW`` characters wins. Unspaced text has none, so the
    cut moves to just after the next punctuation character (Unicode category ``P*``) within
    ``LEAK_PUNCT_WINDOW``, else to the next whitespace in that wider window, else stays at ``end``.
    """
    for probe in range(end, min(len(text), end + LEAK_SPACE_WINDOW)):
        if text[probe].isspace():
            return probe
    wide = min(len(text), end + LEAK_PUNCT_WINDOW)
    for probe in range(end, wide):
        if unicodedata.category(text[probe]).startswith("P"):
            return probe + 1
    for probe in range(end, wide):
        if text[probe].isspace():
            return probe
    return end


def _place_values(
    filler: str, lang_at: Callable[[int], str], rng: random.Random, n_values: int
) -> tuple[str, list[tuple[str, str, int, int, str]]]:
    """Filler with one synthetic value after every ``LEAK_GAP_CHARS`` filler characters.

    ``lang_at`` gives the language of the filler character at an index; each value is drawn from the
    pool of the language at the cut it follows. Returns the text and ``(type, value, start, end, lang)``.
    """
    pieces: list[str] = []
    placed: list[tuple[str, str, int, int, str]] = []
    position = 0
    cursor = 0
    for _ in range(n_values):
        gap = rng.randint(*LEAK_GAP_CHARS)
        if cursor + gap > len(filler):
            cursor = 0
        end = _snap_to_boundary(filler, cursor + gap)
        filler_piece = filler[cursor:end] + " "
        value_lang = lang_at(max(end - 1, 0))
        cursor = end
        pieces.append(filler_piece)
        position += len(filler_piece)
        slot_type = LEAK_TYPES[rng.randrange(len(LEAK_TYPES))]
        pool = _pool(slot_type, value_lang)
        value = pool[rng.randrange(len(pool))]
        pieces.append(value + " ")
        placed.append((slot_type, value, position, position + len(value), value_lang))
        position += len(value) + 1
    return "".join(pieces), placed


def _leak_text(lang: str, n_values: int, part: int) -> tuple[str, list[tuple[str, str, int, int]]]:
    """Filler text with one synthetic value after every 120 to 180 filler characters; ``part`` seeds each document."""
    rng = random.Random(f"{SEED}|leak|{lang}|{n_values}|{part}")
    text, placed = _place_values(_filler_text(lang), lambda _: lang, rng, n_values)
    return text, [(slot_type, value, start, end) for slot_type, value, start, end, _ in placed]


def _mixed_filler(doc: MixedDoc) -> tuple[str, list[tuple[int, str]]]:
    """Slot-free blocks of a mixed document joined, with ``(start offset, language)`` of every block."""
    parts: list[str] = []
    starts: list[tuple[int, str]] = []
    position = 0
    for lang, block in doc.segments():
        stripped = block.strip()
        if not stripped or "{{" in stripped:
            continue
        starts.append((position, lang))
        parts.append(stripped)
        position += len(stripped) + 2
    if not parts:
        raise ValueError(f"no slot-free filler paragraphs in {doc.doc_id}")
    return "\n\n".join(parts), starts


def _mixed_leak_text(
    doc: MixedDoc, n_values: int, part: int
) -> tuple[str, list[tuple[str, str, int, int, str]]]:
    """Leak text over a mixed document's own paragraphs; every value carries the language of its segment."""
    filler, starts = _mixed_filler(doc)
    offsets = [offset for offset, _ in starts]

    def lang_at(index: int) -> str:
        return starts[bisect.bisect_right(offsets, index) - 1][1]

    rng = random.Random(f"{SEED}|leak|mixed|{doc.doc_id}|{n_values}|{part}")
    return _place_values(filler, lang_at, rng, n_values)


def fill_mixed_document(
    doc_key: int, template: str, segment_langs: Sequence[str]
) -> tuple[str, dict[tuple[str, int], str]]:
    """Fill a mixed document's slots, each from the pool of the language of the block it sits in.

    A slot may only occur in blocks of one language; values stay distinct within the document where
    the pool allows.
    """
    values: dict[tuple[str, int], str] = {}
    slot_lang: dict[tuple[str, int], str] = {}
    taken: set[str] = set()
    scope = f"doc{doc_key}"
    blocks: list[str] = []
    for block_lang, block in split_segments(template, segment_langs):

        def _sub(match: re.Match[str], block_lang: str = block_lang) -> str:
            key = (match.group(1), int(match.group(2)))
            if slot_lang.setdefault(key, block_lang) != block_lang:
                raise ValueError(f"slot {key} occurs in {slot_lang[key]} and {block_lang} blocks")
            if key not in values:
                salt = 0
                value = _pick(scope, block_lang, key[0], key[1], salt)
                while value in taken and salt < 50:
                    salt += 1
                    value = _pick(scope, block_lang, key[0], key[1], salt)
                values[key] = value
                taken.add(value)
            return values[key]

        blocks.append(SLOT_PATTERN.sub(_sub, block))
    return "\n\n".join(blocks), values


def _leak_class(slot_type: str) -> str:
    return "identifier" if slot_type in IDENTIFIER_TYPES else "name"


@dataclass(frozen=True)
class _LeakRecord:
    slot_type: str
    kind: str
    literal: bool
    fragment: bool
    fragment_scorable: bool
    violation: bool = False
    segment_lang: str = ""
    document_lang: str = ""


def _rate(hits: int, total: int) -> Optional[float]:
    return hits / total if total else None


def _count(records: list[_LeakRecord], hit: Callable[[_LeakRecord], bool]) -> dict:
    leaks = sum(1 for r in records if hit(r))
    return {"n": len(records), "leaks": leaks, "rate": _rate(leaks, len(records))}


def _summarise(records: list[_LeakRecord]) -> dict:
    """Literal rate over every value; fragment rate over fragment-scorable values only."""
    return {
        "literal": _count(records, lambda r: r.literal),
        "fragment": _count([r for r in records if r.fragment_scorable], lambda r: r.fragment),
    }


def _difference(crossing: dict, control: dict) -> dict:
    def minus(a: Optional[float], b: Optional[float]) -> Optional[float]:
        return a - b if a is not None and b is not None else None

    return {
        kind: {"rate": minus(crossing[kind]["rate"], control[kind]["rate"])}
        for kind in ("literal", "fragment")
    }


def _arms(records: list[_LeakRecord]) -> dict:
    crossing = _summarise([r for r in records if r.kind == "crossing"])
    control = _summarise([r for r in records if r.kind == "control"])
    return {"crossing": crossing, "control": control, "difference": _difference(crossing, control)}


@dataclass
class _ChunkedLeak:
    chunk_texts: list[str]
    spans: list[Optional[Span]]
    sanitized: list[str]
    mapped: list[Span]
    alignments: dict[int, list[Opcode]] = field(default_factory=dict)


def _region_exposure(
    value: str, value_class: str, raw: str, masked: str, opcodes: list[Opcode], start: int, end: int
) -> tuple[bool, bool]:
    """(literal, fragment) exposure of ``value`` inside its own region ``[start, end)`` of ``raw``.

    Only the sanitized text aligned to that region is inspected, so another occurrence of the
    same value elsewhere in the text is never counted.
    """
    lo, hi = sanitized_span(opcodes, max(start, 0), min(end, len(raw)))
    region = masked[lo:hi]
    return has_literal_leak(value, region), has_fragment_leak(value, value_class, region)


def _score_value(
    leak: _ChunkedLeak, slot_type: str, value: str, vs: int, ve: int, document_view: tuple[int, str, str]
) -> Optional[_LeakRecord]:
    """Leak flags for one placed value over every chunk it overlaps; None when no chunk covers it.

    ``document_view`` is ``(offset, raw, masked)`` of a window around the value as the
    whole-document masking shows it. ``violation`` is set when some chunk exposes the value
    (literally or as a fragment, inside its own region) although the whole-document view does not.
    """
    value_class = _leak_class(slot_type)
    scorable = fragment_scorable(value, value_class)
    literal = False
    fragment = False
    touched = False
    for i, span in enumerate(leak.spans):
        if span is None or not (span[0] < ve and span[1] > vs):
            continue
        touched = True
        if i not in leak.alignments:
            leak.alignments[i] = alignment(leak.chunk_texts[i], leak.sanitized[i])
        chunk_literal, chunk_fragment = _region_exposure(
            value, value_class, leak.chunk_texts[i], leak.sanitized[i], leak.alignments[i],
            vs - span[0], ve - span[0],
        )
        literal = literal or chunk_literal
        fragment = fragment or chunk_fragment
    if not touched:
        return None
    offset, raw, masked = document_view
    doc_literal, doc_fragment = _region_exposure(
        value, value_class, raw, masked, alignment(raw, masked), vs - offset, ve - offset
    )
    violation = (literal or fragment) and not (doc_literal or doc_fragment)
    return _LeakRecord(
        slot_type, classify_boundary((vs, ve), leak.mapped), literal, fragment, scorable, violation
    )


async def _leak_block(ctx: ChunkContext, lang: str, timing: dict) -> dict:
    """Leak rates over what production stores: each chunk's projected ``sanitized_text``.

    Documents of ``LEAK_DOC_VALUES`` values are processed one at a time, each with its own map,
    until ``LEAK_MIN_CROSSING`` values cross a chunk edge or ``LEAK_MAX_DOCS`` documents are used.
    """
    records: list[_LeakRecord] = []
    unknown = 0
    fragment_unscorable = 0
    chunk_total = 0
    documents = 0
    elapsed = 0.0
    while documents < LEAK_MAX_DOCS:
        text, placed = _leak_text(lang, LEAK_DOC_VALUES, documents)
        documents += 1
        chunked = await CHUNKERS["pipeline"](
            ctx, "txt", text, lang, AnonymizationMap(document_id=-documents, language=lang)
        )
        elapsed += chunked.detect_s + chunked.cut_s
        chunk_total += len(chunked.texts)
        spans = list(chunked.spans)
        leak = _ChunkedLeak(chunked.texts, spans, chunked.sanitized, spans)
        windows = [
            (max(vs - LEAK_VIEW_PAD, 0), min(ve + LEAK_VIEW_PAD, len(text))) for _, _, vs, ve in placed
        ]
        masked_windows = await asyncio.to_thread(chunked.document_view, windows)
        for (slot_type, value, vs, ve), (w0, w1), masked in zip(placed, windows, masked_windows):
            fragment_unscorable += int(not fragment_scorable(value, _leak_class(slot_type)))
            record = _score_value(leak, slot_type, value, vs, ve, (w0, text[w0:w1], masked))
            if record is None:
                unknown += 1
            else:
                records.append(record)
        if sum(1 for r in records if r.kind == "crossing") >= LEAK_MIN_CROSSING:
            break
    timing.setdefault("leak_sanitize_s", {})[lang] = round(elapsed, 3)
    n_values = documents * LEAK_DOC_VALUES

    crossing_count = sum(1 for r in records if r.kind == "crossing")
    return {
        "values": n_values,
        "documents": documents,
        "chunks": chunk_total,
        "unmapped_chunks": 0,
        "unknown": unknown,
        "fragment_unscorable": fragment_unscorable,
        "below_min_crossing": crossing_count < LEAK_MIN_CROSSING,
        "invariant_violations": sum(1 for r in records if r.violation),
        **_arms(records),
        "excluding_organization_name": _arms([r for r in records if r.slot_type != SEPARATED_LEAK_TYPE]),
        "by_type": {
            slot_type: {
                **_summarise([r for r in records if r.slot_type == slot_type]),
                "fragment_unscorable": sum(
                    1 for r in records if r.slot_type == slot_type and not r.fragment_scorable
                ),
            }
            for slot_type in LEAK_TYPES
        },
    }


def _segment_block(records: list[_LeakRecord]) -> dict:
    """Literal and fragment leak, boundary arms and per-type counts for records of one segment language."""
    return {
        "values": len(records),
        **_summarise(records),
        "arms": _arms(records),
        "by_type": {
            slot_type: _summarise([r for r in records if r.slot_type == slot_type]) for slot_type in LEAK_TYPES
        },
    }


def _match_split(records: list[_LeakRecord]) -> dict:
    """``matched`` (segment language == masking language) and ``mismatched`` blocks of ``records``."""
    return {
        "matched": _segment_block([r for r in records if r.segment_lang == r.document_lang]),
        "mismatched": _segment_block([r for r in records if r.segment_lang != r.document_lang]),
    }


def segment_leak_report(records: list[_LeakRecord]) -> dict:
    """Leak broken down by the language of the segment a value sits in.

    ``by_segment_lang`` groups on the value's own segment; ``by_detection_match`` splits the same records
    into values whose segment language equals the language the document was masked with (``matched``)
    and values in any other language (``mismatched``), the part a single document language handles badly.
    ``by_segment_lang_match`` makes that split per segment language, so the leak of a language's values
    in documents masked with another language reads directly against the same language's values in
    documents masked with it.
    """
    langs = sorted({r.segment_lang for r in records})
    return {
        "by_segment_lang": {lang: _segment_block([r for r in records if r.segment_lang == lang]) for lang in langs},
        "by_detection_match": _match_split(records),
        "by_segment_lang_match": {
            lang: _match_split([r for r in records if r.segment_lang == lang]) for lang in langs
        },
    }


async def _score_leak_text(
    ctx: ChunkContext, text: str, placed: list[tuple[str, str, int, int, str]], lang: str, document_id: int
) -> tuple[list[Optional[_LeakRecord]], int, float]:
    """Mask ``text`` with ``lang`` the production way and score every placed value.

    Returns one record per placed value (None when no chunk covers it), each carrying the value's own
    language and the masking language, then the chunk count and the detect-plus-cut seconds.
    """
    chunked = await CHUNKERS["pipeline"](
        ctx, "txt", text, lang, AnonymizationMap(document_id=document_id, language=lang)
    )
    spans = list(chunked.spans)
    leak = _ChunkedLeak(chunked.texts, spans, chunked.sanitized, spans)
    windows = [(max(vs - LEAK_VIEW_PAD, 0), min(ve + LEAK_VIEW_PAD, len(text))) for _, _, vs, ve, _ in placed]
    masked_windows = await asyncio.to_thread(chunked.document_view, windows)
    records: list[Optional[_LeakRecord]] = []
    for (slot_type, value, vs, ve, value_lang), (w0, w1), masked in zip(placed, windows, masked_windows):
        record = _score_value(leak, slot_type, value, vs, ve, (w0, text[w0:w1], masked))
        records.append(None if record is None else replace(record, segment_lang=value_lang, document_lang=lang))
    return records, len(chunked.texts), chunked.detect_s + chunked.cut_s


async def _mixed_reference_block(ctx: ChunkContext, first_id: int) -> tuple[dict, float]:
    """Single-language leak at the mixed slice's density, the like-for-like yardstick for its matched values.

    The single-language ``leak`` block uses ``LEAK_DOC_VALUES`` values per document; with a pool of
    ``POOL_SIZE`` per type and language most values recur there, and a detected person name masks its
    other occurrences too. The mixed documents carry ``MIXED_LEAK_DOC_VALUES`` values, so this block
    places the same number per document over each language's own filler, ``MIXED_LEAK_ROUNDS`` times.
    """
    block: dict = {}
    elapsed = 0.0
    document_id = first_id
    for lang in LANGS:
        records: list[_LeakRecord] = []
        for part in range(MIXED_LEAK_ROUNDS):
            text, placed = _leak_text(lang, MIXED_LEAK_DOC_VALUES, part)
            document_id -= 1
            scored, _, seconds = await _score_leak_text(
                ctx, text, [(*p, lang) for p in placed], lang, document_id
            )
            elapsed += seconds
            records.extend(r for r in scored if r is not None)
        block[lang] = _segment_block(records)
    return block, elapsed


async def _mixed_leak_block(ctx: ChunkContext, timing: dict) -> dict:
    """Leak of the mixed-language slice, scored like ``_leak_block`` but broken down by segment language.

    Each mixed document is masked with the one language detection gives its text, as production does,
    so values in the other language's segments show what that single choice costs. Reported apart from
    ``leak`` so the single-language numbers stay comparable; ``single_language_reference`` is the
    single-language leak at this slice's value density, the rate matched values compare against.
    """
    records: list[_LeakRecord] = []
    documents: dict[str, dict] = {}
    unknown = 0
    chunk_total = 0
    elapsed = 0.0
    run = 0
    for meta in load_mixed_docs():
        langs: dict[str, int] = {}
        detected: list[str] = []
        doc_records: list[_LeakRecord] = []
        for part in range(MIXED_LEAK_ROUNDS):
            text, placed = _mixed_leak_text(meta, MIXED_LEAK_DOC_VALUES, part)
            document_lang = detect_language(text, fallback=meta.lang)
            detected.append(document_lang)
            run += 1
            scored, chunks, seconds = await _score_leak_text(ctx, text, placed, document_lang, -run)
            elapsed += seconds
            chunk_total += chunks
            for (_, _, _, _, segment_lang), record in zip(placed, scored):
                langs[segment_lang] = langs.get(segment_lang, 0) + 1
                if record is None:
                    unknown += 1
                else:
                    doc_records.append(record)
        records.extend(doc_records)
        documents[meta.doc_id] = {
            "main_lang": meta.lang,
            "layout": meta.layout,
            "detected_lang": sorted(set(detected)),
            "values_by_segment_lang": dict(sorted(langs.items())),
            "leak_by_segment_lang": {
                lang: _summarise([r for r in doc_records if r.segment_lang == lang]) for lang in sorted(langs)
            },
        }
    reference, reference_s = await _mixed_reference_block(ctx, -run)
    timing["mixed_leak_sanitize_s"] = round(elapsed, 3)
    timing["mixed_reference_sanitize_s"] = round(reference_s, 3)
    return {
        "digest": mixed_fixture_digest(),
        "values": len(load_mixed_docs()) * MIXED_LEAK_ROUNDS * MIXED_LEAK_DOC_VALUES,
        "chunks": chunk_total,
        "unknown": unknown,
        "invariant_violations": sum(1 for r in records if r.violation),
        "documents": documents,
        **segment_leak_report(records),
        "single_language_reference": reference,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  RUN
# ═══════════════════════════════════════════════════════════════════════════


def _git_sha() -> Optional[str]:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
            cwd=Path(__file__).resolve().parent,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout.strip() or None


def _cpu_model() -> str:
    if platform.system() == "Darwin":
        completed = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, check=False
        )
        if completed.returncode == 0 and completed.stdout.strip():
            return completed.stdout.strip()
    elif Path("/proc/cpuinfo").exists():
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or platform.machine()


def _run_info() -> dict:
    return {
        "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": _git_sha(),
        "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
        "cpu_model": _cpu_model(),
        "cpu_cores": os.cpu_count(),
        "torch_threads": torch.get_num_threads(),
    }


def _build_cases(
    sanitizer: PIISanitizer,
    docs: dict[int, EvalDocument],
    queries: list[EvalQuery],
    by_id: dict[str, int],
) -> list[QueryCase]:
    """Per query: the filled text, its language, gold spans, and the raw and sanitized variants per scope.

    Manual scope (dense, bm25, hybrid) sanitizes against a copy of the query's
    document map; auto scope sanitizes against an empty map, as chat does when no
    document is selected.
    """
    cases: list[QueryCase] = []
    for query in queries:
        doc_key = by_id[query.doc_id] if query.doc_id is not None else None
        doc = docs[doc_key] if doc_key is not None else None
        fallback = doc.meta.lang if doc is not None else query.lang
        text = fill_query(query.text, doc_key, fallback, doc.values if doc is not None else {})
        lang = detect_language(
            text, fallback=fallback, context_language=doc.meta.lang if doc is not None else None
        )
        gold = resolve_gold_spans(query, doc.filled) if doc is not None else []
        variants: dict[tuple[str, str], str] = {("raw", "doc"): text, ("raw", "auto"): text}
        if doc is not None:
            variants[("sanitized", "doc")] = sanitizer.sanitize(
                text, lang, copy.deepcopy(doc.anon_map)
            ).sanitized_text
        variants[("sanitized", "auto")] = sanitizer.sanitize(
            text, lang, AnonymizationMap(document_id=0, language=lang)
        ).sanitized_text
        cases.append(QueryCase(query=query, doc_key=doc_key, lang=lang, gold=gold, variants=variants))
    return cases


async def run_benchmark(
    config: BenchmarkConfig,
    store: VectorStore,
    bm25: BM25Retriever,
    settings: AppSettings,
    sanitizer: PIISanitizer,
) -> dict:
    """Index the evaluation corpus, run every retriever and query mode, and assemble the result document."""
    DetectorFactory.seed = 42
    pipeline = DocumentPipeline(settings)
    ctx = ChunkContext(pipeline, sanitizer)
    chunker = CHUNKERS[config.chunker]
    timing: dict = {}
    wall_started = time.perf_counter()

    # Ingestion: detect once per document, cut and project the chunks, then index the raw chunk slices.
    documents: dict[int, EvalDocument] = {}
    by_id: dict[str, int] = {}
    for key, meta in enumerate(load_docs(), start=1):
        template = meta.read_text()
        filled, values = fill_document(key, meta.lang, template)
        if "{{PII:" in filled:
            raise ValueError(f"unfilled slot left in {meta.doc_id}")
        documents[key] = EvalDocument(
            key=key, meta=meta, filled=filled, values=values,
            anon_map=AnonymizationMap(document_id=key, language=meta.lang),
        )
        by_id[meta.doc_id] = key

    chunks: dict[int, IndexedChunk] = {}
    next_chunk_id = 1
    doc_chunk_texts: dict[int, list[str]] = {}
    detect_s = 0.0
    cut_s = 0.0
    for doc in documents.values():
        chunked = await chunker(ctx, doc.meta.file_format, doc.filled, doc.meta.lang, doc.anon_map)
        detect_s += chunked.detect_s
        cut_s += chunked.cut_s
        doc_chunk_texts[doc.key] = chunked.texts
        for text, span in zip(chunked.texts, chunked.spans):
            chunks[next_chunk_id] = IndexedChunk(next_chunk_id, doc.key, text, span)
            doc.chunk_ids.append(next_chunk_id)
            next_chunk_id += 1
    embedder_counter = _EmbedderTokens()
    over_limit = sum(1 for c in chunks.values() if embedder_counter.count(c.text) > embedder_counter.cap)
    timing["chunk_s"] = round(cut_s, 3)
    timing["sanitize_s"] = round(detect_s, 3)
    unmapped = 0

    index_started = time.perf_counter()
    for doc in documents.values():
        if doc.chunk_ids:
            store.index_document(doc.key, doc.chunk_ids, doc_chunk_texts[doc.key])
            bm25.index_document(doc.key, doc.chunk_ids, doc_chunk_texts[doc.key])
    timing["embed_index_s"] = round(time.perf_counter() - index_started, 3)
    timing["ingest_embed_s"] = round(timing["chunk_s"] + timing["sanitize_s"] + timing["embed_index_s"], 3)
    dim = int(store._encode_texts(["dimension probe"]).shape[1])

    cases = _build_cases(sanitizer, documents, load_queries(), by_id)
    index = Index(store=store, bm25=bm25, doc_keys=list(documents), chunks=chunks)

    results: dict = {}
    latency: dict = {}
    for name in config.retrievers:
        retriever = RETRIEVERS[name]
        results[name] = {}
        for mode in config.query_modes:
            scoped = [c for c in cases if retriever.scope == "auto" or c.doc_key is not None]
            results[name][mode] = _evaluate_retriever(index, retriever, mode, scoped)
        latency[name] = _measure_latency(index, retriever, cases, config.headline_mode, config.latency_repeats)

    leak = {}
    for lang in LANGS:
        leak[lang] = await _leak_block(ctx, lang, timing)
    mixed_leak = await _mixed_leak_block(ctx, timing)

    answerable = [c for c in cases if c.doc_key is not None]
    timing["wall_s"] = round(time.perf_counter() - wall_started, 3)
    timing["latency"] = latency
    return {
        "schema_version": SCHEMA_VERSION,
        "run": _run_info(),
        "config": {
            "embedding_model": config.embedding_model,
            "revision": require_pin(config.embedding_model).revision,
            "dim": dim,
            "chunker": {"name": config.chunker, "params": CHUNKER_DESCRIPTIONS[config.chunker]},
            "retrievers": list(config.retrievers),
            "query_modes": list(config.query_modes),
            "headline_mode": config.headline_mode,
            "top_k": TOP_K,
            "auto_threshold": _DEFAULT_RELEVANCE_THRESHOLD,
            "seed": SEED,
            "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
            "latency_repeats": config.latency_repeats,
            "settings": {
                "chunk_size": settings.chunk_size,
                "chunk_overlap": settings.chunk_overlap,
                "pdf_chunk_size": settings.pdf_chunk_size,
                "ner_layer": settings.use_ner_layer,
                "ollama_layer": False,
            },
        },
        "eval": {
            "digest": fixture_digest(),
            "counts": {
                "documents": len(documents),
                "documents_by_lang": {lang: sum(1 for d in documents.values() if d.meta.lang == lang) for lang in LANGS},
                "queries": len(cases),
                "answerable": len(answerable),
                "negative": len(cases) - len(answerable),
                "answerable_by_type": {
                    qtype: sum(1 for c in answerable if c.query.type == qtype) for qtype in ANSWERABLE_TYPES
                },
            },
            "index": {
                "chunks": len(chunks), "unmapped_chunks": unmapped,
                "over_embedder_limit": over_limit,
            },
        },
        "results": results,
        "leak": leak,
        "mixed_leak": mixed_leak,
        "timing": timing,
    }


def _fmt_ci(metric: dict) -> str:
    low, high = metric["ci95"]
    return f"{metric['mean']:.3f} [{low:.3f}, {high:.3f}]"


def _fmt_or_dash(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.3f}"


def _leak_table(leak: dict, excluding: bool) -> list[str]:
    header = ("| lang | values | crossing n | crossing literal | crossing fragment | control literal "
              "| control fragment | difference literal | difference fragment | fragment unscorable |")
    rows = []
    for lang, block in leak.items():
        view = block["excluding_organization_name"] if excluding else block
        crossing, control, diff = view["crossing"], view["control"], view["difference"]
        rows.append(
            f"| {lang} | {block['values']} | {crossing['literal']['n']} | "
            f"{_fmt_or_dash(crossing['literal']['rate'])} | {_fmt_or_dash(crossing['fragment']['rate'])} | "
            f"{_fmt_or_dash(control['literal']['rate'])} | {_fmt_or_dash(control['fragment']['rate'])} | "
            f"{_fmt_or_dash(diff['literal']['rate'])} | {_fmt_or_dash(diff['fragment']['rate'])} | "
            f"{block['fragment_unscorable']} |"
        )
    return [header, "|---|---|---|---|---|---|---|---|---|---|", *rows]


def render_markdown(result: dict) -> str:
    lines = [
        f"Retrieval benchmark, model `{result['config']['embedding_model']}` "
        f"(revision {result['config']['revision'][:12]}), chunker `{result['config']['chunker']['name']}`, "
        f"digest `{result['eval']['digest'][:12]}`",
        "",
        "| retriever | mode | scope | n | Recall@5 | Recall@10 | MRR | nDCG@10 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, modes in result["results"].items():
        for mode, block in modes.items():
            overall = block["overall"]
            if overall["n"] == 0:
                continue
            lines.append(
                f"| {name} | {mode} | overall | {overall['n']} | "
                + " | ".join(_fmt_ci(overall[m]) for m in METRICS) + " |"
            )
    headline = result["config"]["headline_mode"]
    lines += ["", f"Per language, headline mode `{headline}`:", "",
              "| retriever | lang | n | Recall@5 | Recall@10 | MRR | nDCG@10 |", "|---|---|---|---|---|---|---|"]
    for name, modes in result["results"].items():
        if headline not in modes:
            continue
        for lang in LANGS:
            block = modes[headline]["by_lang"][lang]
            if block["n"]:
                lines.append(f"| {name} | {lang} | {block['n']} | " + " | ".join(_fmt_ci(block[m]) for m in METRICS) + " |")
    for name, modes in result["results"].items():
        for mode, block in modes.items():
            if "negatives" in block:
                neg = block["negatives"]
                lines.append(
                    f"\n{name} ({mode}): false retrieval on {neg['n']} negatives = "
                    f"{_fmt_ci(neg['false_retrieval'])}"
                )
    lines += ["", "Leak block (record only). Literal: the verbatim value survives. Fragment: a recoverable "
              "part of the value survives at its position (definitions in tests/retrieval_metrics.py).", ""]
    lines += _leak_table(result["leak"], excluding=False)
    lines += ["", f"Excluding {SEPARATED_LEAK_TYPE} (detected by NER alone):", ""]
    lines += _leak_table(result["leak"], excluding=True)
    lines += ["", "Leak rate by type, literal / fragment (leaks/n):", "",
              "| type | " + " | ".join(result["leak"]) + " |", "|---|" + "---|" * len(result["leak"])]
    for slot_type in LEAK_TYPES:
        cells = []
        for block in result["leak"].values():
            stats = block["by_type"][slot_type]
            cells.append(
                f"{_fmt_or_dash(stats['literal']['rate'])} ({stats['literal']['leaks']}/{stats['literal']['n']}) / "
                f"{_fmt_or_dash(stats['fragment']['rate'])} ({stats['fragment']['leaks']}/{stats['fragment']['n']})"
            )
        lines.append(f"| {slot_type} | " + " | ".join(cells) + " |")
    low_crossing = [lang for lang, b in result["leak"].items() if b["below_min_crossing"]]
    if low_crossing:
        lines.append(f"\nFewer than {LEAK_MIN_CROSSING} crossing values for: {', '.join(low_crossing)}")
    violations = ", ".join(f"{lang} {b['invariant_violations']}" for lang, b in result["leak"].items())
    lines.append(f"\nInvariant violations (chunk exposes a value the whole-document view masks): {violations}")
    lines += _mixed_leak_table(result.get("mixed_leak"))
    return "\n".join(lines)


def _mixed_leak_table(mixed: Optional[dict]) -> list[str]:
    """Rows of the mixed-language leak, literal / fragment leaks over n per segment language."""
    if not mixed:
        return []
    lines = ["", "Mixed-language slice, leak by segment language, literal / fragment (leaks/n):", "",
             "| segment | values | literal | fragment |", "|---|---|---|---|"]
    groups = {**mixed["by_segment_lang"], **{f"({k})": v for k, v in mixed["by_detection_match"].items()}}
    for name, block in groups.items():
        lines.append(f"| {name} | {block['values']} | {_fmt_leaks(block['literal'])} | {_fmt_leaks(block['fragment'])} |")
    lines += ["", "Per segment language: masked with its own language (matched), with another (mismatched), "
              "and the single-language reference at the same value density, literal / fragment (leaks/n):", "",
              "| segment | matched | mismatched | reference |", "|---|---|---|---|"]
    reference = mixed.get("single_language_reference", {})
    for lang, split in mixed["by_segment_lang_match"].items():
        cells = [split["matched"], split["mismatched"]] + ([reference[lang]] if lang in reference else [])
        rendered = [f"{_fmt_leaks(c['literal'])} / {_fmt_leaks(c['fragment'])}" for c in cells]
        lines.append(f"| {lang} | " + " | ".join(rendered + ["-"] * (3 - len(rendered))) + " |")
    return lines


def _fmt_leaks(stats: dict) -> str:
    return f"{_fmt_or_dash(stats['rate'])} ({stats['leaks']}/{stats['n']})"


# ═══════════════════════════════════════════════════════════════════════════
#  TESTS
# ═══════════════════════════════════════════════════════════════════════════

def gate_failures(result: dict) -> list[str]:
    """Every gate criterion the run misses, as readable lines; empty when the run passes.

    Leak, per language: no sampled value may be exposed by a chunk's projected text (inside the
    value's own region) while the whole-document masking hides it (``invariant_violations`` must
    be 0), and no EMAIL/PHONE/IBAN value may leak literally. The crossing-minus-control
    fragment difference, the absolute rates and the crossing sample size are reported, not gated:
    the chunker never cuts a detected value, so the crossing arm holds only values the detector
    missed, which makes that difference a composition effect rather than a boundary effect.
    Quality guard (production embedder, ``pipeline`` chunker): the sanitized hybrid headline
    keeps Recall@5 and MRR at or above the re-based floors.
    """
    failures: list[str] = []
    for lang, block in result["leak"].items():
        violations = block["invariant_violations"]
        if violations:
            failures.append(
                f"leak {lang}: {violations} value occurrences exposed by a chunk but masked "
                "in the whole-document view, must be 0"
            )
        for slot_type in GATE_ZERO_LITERAL_TYPES:
            literal = block["by_type"][slot_type]["literal"]
            if literal["leaks"]:
                failures.append(f"leak {lang}: {slot_type} literal leak {literal['leaks']} of {literal['n']}, must be 0")
    config = result["config"]
    if config["chunker"]["name"] == "pipeline" and config["embedding_model"] == _DEFAULT_ST_MODEL:
        headline = result["results"].get("hybrid", {}).get("sanitized", {}).get("overall")
        if not headline or not headline.get("n"):
            failures.append("quality guard: no sanitized hybrid headline in this run")
        else:
            recall = headline["recall@5"]["mean"]
            reciprocal = headline["mrr"]["mean"]
            if recall < GATE_MIN_RECALL_AT_5:
                failures.append(f"quality guard: sanitized hybrid Recall@5 {recall:.3f} is below {GATE_MIN_RECALL_AT_5}")
            if reciprocal < GATE_MIN_MRR:
                failures.append(f"quality guard: sanitized hybrid MRR {reciprocal:.3f} is below {GATE_MIN_MRR}")
    return failures


def _load_embedder_or_skip(store: VectorStore):
    try:
        return store._get_model()
    except ModelIntegrityError as exc:
        pytest.skip(f"pinned embedder not in the local HF cache (offline): {exc}")


def _assert_pinned(store: VectorStore, model_name: str) -> None:
    pin = require_pin(model_name)
    snapshot = ensure_pinned_snapshot(pin)
    model = store._get_model()
    loaded_from = Path(model[0].auto_model.config._name_or_path).resolve()
    assert loaded_from == snapshot.resolve(), (
        f"process-wide embedder is not {model_name}@{pin.revision}: loaded from {loaded_from}"
    )


_PARAGRAPHS = [
    "The quarterly maintenance schedule for the cooling plant lists filter "
    "replacement every ninety days and a full coolant flush once per year.",
    "Employees accrue vacation leave monthly; unused days up to a maximum of "
    "ten may be carried over into the following calendar year.",
    "The archive migration moved all scanned invoices to cold storage, "
    "reducing the monthly storage bill by roughly forty percent.",
    "Visitors must register at the front desk, wear a visible badge and be "
    "escorted whenever they leave the lobby area.",
]


async def test_smoke_hybrid_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(crypto, "_ENCRYPTION_KEY", crypto.generate_key())

    store = VectorStore(base_dir=tmp_path / "faiss", model_name=EMBEDDING_MODEL)
    bm25 = BM25Retriever(base_dir=tmp_path / "bm25")
    _load_embedder_or_skip(store)
    _assert_pinned(store, EMBEDDING_MODEL)
    pin = require_pin(EMBEDDING_MODEL)

    settings = _make_settings(chunk_size=200, chunk_overlap=0, use_ner=False)
    ctx = ChunkContext(DocumentPipeline(settings), _build_sanitizer(settings))
    chunked = await CHUNKERS["pipeline"](
        ctx, "txt", "\n\n".join(_PARAGRAPHS), "en", AnonymizationMap(document_id=1, language="en")
    )
    texts = chunked.texts
    assert len(texts) >= 2

    chunk_ids = list(range(1, len(texts) + 1))
    store.index_document(1, chunk_ids, texts)
    bm25.index_document(1, chunk_ids, texts)

    target = next(i for i, t in enumerate(texts) if "vacation leave" in t)
    results = store.hybrid_search(
        1, "how many vacation days can be carried over", top_k=3, bm25_retriever=bm25
    )
    ranked = [cid for cid, _ in results]
    print(
        f"\nmodel={EMBEDDING_MODEL} revision={pin.revision} chunks={len(texts)} "
        f"ranked={ranked} target={chunk_ids[target]}"
    )
    assert ranked[0] == chunk_ids[target]


async def test_full_retrieval_benchmark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config()
    monkeypatch.setattr(crypto, "_ENCRYPTION_KEY", crypto.generate_key())

    store = VectorStore(base_dir=tmp_path / "faiss", model_name=config.embedding_model)
    bm25 = BM25Retriever(base_dir=tmp_path / "bm25")
    _load_embedder_or_skip(store)
    _assert_pinned(store, config.embedding_model)

    settings = _make_settings(chunk_overlap=config.chunk_overlap)
    sanitizer = _build_sanitizer(settings)
    result = await run_benchmark(config, store, bm25, settings, sanitizer)

    if config.out is not None:
        config.out.parent.mkdir(parents=True, exist_ok=True)
        config.out.write_text(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print("\n" + render_markdown(result))
    print(
        f"\nwall={result['timing']['wall_s']}s chunk={result['timing']['chunk_s']}s "
        f"embed_index={result['timing']['embed_index_s']}s detect={result['timing']['sanitize_s']}s "
        f"ingest_embed={result['timing']['ingest_embed_s']}s"
    )

    assert result["eval"]["counts"]["queries"] == 140
    assert result["eval"]["index"]["chunks"] > 0
    failures = gate_failures(result)
    assert not failures, "retrieval gate failed:\n" + "\n".join(failures)
