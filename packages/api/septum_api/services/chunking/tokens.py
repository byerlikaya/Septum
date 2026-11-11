"""Token counting against the local embedder's tokenizer, used as a chunk cap.

The tokenizer comes from an already loaded embedding model, so nothing is
downloaded and no network is touched. Counting never truncates: the chunker
probes slices of roughly one budget of characters, and a truncating tokenizer
would report a falsely small length for a long probe.
"""

from __future__ import annotations

import logging
from typing import Any

_LENGTH_LOGGER = "transformers.tokenization_utils_base"


class _DropLengthWarning(logging.Filter):
    """Drop only the 'sequence length is longer than the maximum' warning."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "longer than the specified maximum sequence length" not in record.getMessage()


_LENGTH_FILTER = _DropLengthWarning()


class TokenCounter:
    """Counts tokens of a text with ``tokenizer`` and carries the token cap.

    ``cap`` is the embedder's input limit minus the special tokens the
    embedder adds around every text. ``count`` excludes special tokens and
    is not truncated.
    """

    def __init__(self, tokenizer: Any, cap: int) -> None:
        if cap < 1:
            raise ValueError("token cap must be at least 1")
        self._tokenizer = tokenizer
        self.cap = cap
        logging.getLogger(_LENGTH_LOGGER).addFilter(_LENGTH_FILTER)

    @classmethod
    def from_model(cls, model: Any) -> "TokenCounter":
        """Build from a loaded SentenceTransformer (its tokenizer and max_seq_length)."""
        tokenizer = model.tokenizer
        specials = tokenizer.num_special_tokens_to_add(pair=False)
        return cls(tokenizer, int(model.max_seq_length) - specials)

    def count(self, text: str) -> int:
        """Number of tokens in ``text`` without special tokens or truncation."""
        if not text:
            return 0
        encoded = self._tokenizer(
            text,
            add_special_tokens=False,
            truncation=False,
            return_attention_mask=False,
            return_token_type_ids=False,
        )
        return len(encoded["input_ids"])
