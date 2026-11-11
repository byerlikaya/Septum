"""Token cap for chunker v2: the local embedder's tokenizer, offline, never truncating."""

from __future__ import annotations

import logging
import os
import random
from typing import Any, Dict, List

import pytest

from septum_api.services.chunking import chunk_blocks
from septum_api.services.chunking.tokens import TokenCounter
from septum_api.services.ingestion.base import TextBlock, join_blocks
from septum_api.services.ingestion.sheet_text import render_sheet

PARAGRAPHS = [
    "The quarterly report shows revenue growth across all regions, driven by new contracts.",
    "Yönetim kurulu toplantısında alınan kararlar, ilgili birimlere yazılı olarak bildirilmiştir.",
    "本季度报告显示所有地区的收入均有增长，主要由新签合同推动，客户满意度也明显提高。",
    "Invoice 2024-001 totals 1,250.00 EUR; payment is due within thirty days of receipt.",
]


@pytest.fixture(scope="module")
def counter() -> TokenCounter:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from septum_api.services.vector_store import get_shared_sentence_transformer

    return TokenCounter.from_model(get_shared_sentence_transformer())


def _blocks(count: int, seed: int = 7) -> List[TextBlock]:
    rng = random.Random(seed)
    return [TextBlock(" ".join(rng.choices(PARAGRAPHS, k=3)), "paragraph") for _ in range(count)]


def test_cap_is_input_limit_minus_special_tokens(counter: TokenCounter) -> None:
    assert counter.cap == 126


def test_count_is_not_truncated(counter: TokenCounter) -> None:
    long_text = "hello world " * 2000
    assert counter.count(long_text) > 10 * counter.cap
    assert counter.count(long_text) > counter.count(long_text[: len(long_text) // 2])
    assert counter.count("") == 0


def test_count_grows_with_prefix(counter: TokenCounter) -> None:
    text = join_blocks(_blocks(20))
    lengths = [counter.count(text[:n]) for n in range(0, len(text), 37)]
    assert lengths == sorted(lengths)


@pytest.mark.parametrize("strategy", ["structured", "sliding"])
def test_no_chunk_exceeds_the_token_cap(counter: TokenCounter, strategy: str) -> None:
    blocks = _blocks(60)
    text = join_blocks(blocks)
    chunks = chunk_blocks(
        blocks,
        strategy,
        chunk_size=2000,
        overlap=200,
        length_fn=counter.count,
        max_length=counter.cap,
    )
    assert len(chunks) > 1
    assert max(counter.count(text[c.start : c.end]) for c in chunks) <= counter.cap


def test_table_chunks_stay_under_the_token_cap(counter: TokenCounter) -> None:
    grid = [["header"] * 12] + [[f"value{r}x{c} lorem ipsum" for c in range(12)] for r in range(60)]
    blocks, _, _ = render_sheet("Data", grid)
    text = join_blocks(blocks)
    chunks = chunk_blocks(
        blocks,
        "table",
        spreadsheet_chunk_size=200,
        length_fn=counter.count,
        max_length=counter.cap,
    )
    assert len(chunks) > 1
    assert max(counter.count(text[c.start : c.end]) for c in chunks) <= counter.cap


def test_audio_chunks_stay_under_the_token_cap(counter: TokenCounter) -> None:
    blocks = [
        TextBlock(
            f" segment {n}: " + " ".join(PARAGRAPHS),
            "segment",
            t_start=n * 5.0,
            t_end=(n + 1) * 5.0,
        )
        for n in range(30)
    ]
    text = join_blocks(blocks)
    chunks = chunk_blocks(
        blocks,
        "audio",
        audio_chunk_size=3600.0,
        length_fn=counter.count,
        max_length=counter.cap,
    )
    assert len(chunks) > 1
    assert max(counter.count(text[c.start : c.end]) for c in chunks) <= counter.cap


def test_a_single_protected_range_may_push_a_chunk_over_the_cap(counter: TokenCounter) -> None:
    blocks = [TextBlock("hello world " * 200, "paragraph")]
    text = join_blocks(blocks)
    protected = (300, 1500)
    assert counter.count(text[protected[0] : protected[1]]) > counter.cap
    chunks = chunk_blocks(
        blocks,
        "sliding",
        protected=[protected],
        chunk_size=2000,
        overlap=0,
        length_fn=counter.count,
        max_length=counter.cap,
    )
    over = [c for c in chunks if counter.count(c.text) > counter.cap]
    assert len(over) == 1
    assert (over[0].start, over[0].end) == protected
    for chunk in chunks:
        assert not (chunk.start < protected[0] < chunk.end and chunk.end < protected[1])
        assert not (chunk.start > protected[0] and chunk.start < protected[1])


def test_log_filter_drops_only_the_length_warning(
    counter: TokenCounter, caplog: pytest.LogCaptureFixture
) -> None:
    logger = logging.getLogger("transformers.tokenization_utils_base")
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        logger.warning(
            "Token indices sequence length is longer than the specified maximum sequence length "
            "for this model (900 > 512). Running this sequence through the model will result in "
            "indexing errors"
        )
        logger.warning("some other tokenizer warning")
        logger.error("tokenizer failure")
    messages = [r.getMessage() for r in caplog.records if r.name == logger.name]
    assert messages == ["some other tokenizer warning", "tokenizer failure"]


def test_constructing_counters_does_not_stack_log_filters() -> None:
    logger = logging.getLogger("transformers.tokenization_utils_base")
    TokenCounter(lambda text, **kwargs: {"input_ids": []}, cap=10)
    before = len(logger.filters)
    for _ in range(5):
        TokenCounter(lambda text, **kwargs: {"input_ids": []}, cap=10)
    assert len(logger.filters) == before


def test_empty_text_counts_zero_without_touching_the_tokenizer() -> None:
    def explode(*args: Any, **kwargs: Any) -> Dict[str, List[int]]:
        raise AssertionError("tokenizer must not be called for empty text")

    assert TokenCounter(explode, cap=10).count("") == 0


class _FakeTokenizer:
    def __init__(self, specials: int) -> None:
        self._specials = specials

    def num_special_tokens_to_add(self, pair: bool = False) -> int:
        assert pair is False
        return self._specials

    def __call__(self, text: str, **kwargs: Any) -> Dict[str, List[int]]:
        assert kwargs["add_special_tokens"] is False
        assert kwargs["truncation"] is False
        return {"input_ids": list(range(len(text.split())))}


class _FakeModel:
    def __init__(self, max_seq_length: int, specials: int) -> None:
        self.max_seq_length = max_seq_length
        self.tokenizer = _FakeTokenizer(specials)


@pytest.mark.parametrize(
    ("max_seq_length", "specials", "expected"),
    [(512, 2, 510), (256, 4, 252), (128, 0, 128), (384, 3, 381)],
)
def test_from_model_cap_is_max_seq_length_minus_specials(
    max_seq_length: int, specials: int, expected: int
) -> None:
    counter = TokenCounter.from_model(_FakeModel(max_seq_length, specials))
    assert counter.cap == expected
    assert counter.count("one two three") == 3


def test_from_model_rejects_a_model_with_no_room_for_text() -> None:
    with pytest.raises(ValueError):
        TokenCounter.from_model(_FakeModel(max_seq_length=2, specials=2))
