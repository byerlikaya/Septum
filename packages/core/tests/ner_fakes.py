"""Stand-ins for a HuggingFace token-classification pipeline, for windowing tests.

The fake tokenizer treats each whitespace-separated word as one token, unless
``groups`` gives a word several tokens that share its characters (as NFKC
expands one compatibility character). ``drift`` adds phantom pieces to
re-tokenized text, as WordPiece does at a cut. The fake pipeline truncates its
input at ``budget`` content tokens, as the real one does, and records it, so a
test can assert that no window was silently cut. The budget is passed in: the
production derivation is tested against the fake's embedding shape, not
re-implemented here.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

NAME = ("Ada", "Lovelace")


class FakeTokenizer:
    def __init__(
        self,
        model_max_length: int,
        specials: int = 2,
        drift: int = 0,
        groups: dict[str, int] | None = None,
    ) -> None:
        self.model_max_length = model_max_length
        self._specials = specials
        self._drift = drift
        self._groups = groups or {}

    def num_special_tokens_to_add(self, pair: bool = False) -> int:
        return self._specials

    def pieces(self, text: str) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        for m in re.finditer(r"\S+", text):
            out.extend([m.span()] * self._groups.get(m.group(), 1))
        return out

    def __call__(self, text, add_special_tokens=False, return_offsets_mapping=False):
        assert add_special_tokens is False
        if isinstance(text, list):
            return {"input_ids": [self._ids(chunk) for chunk in text]}
        pieces = self.pieces(text)
        if return_offsets_mapping:
            return {"offset_mapping": pieces, "input_ids": list(range(len(pieces)))}
        return {"input_ids": self._ids(text)}

    def _ids(self, text: str) -> list[int]:
        return list(range(len(self.pieces(text)) + self._drift))


class FakePipeline:
    """Tags whole names, and the edge word of a name cut by a chunk boundary.

    A real token-classification model reports partial entities at the edge of
    its input; this does the same so the seam logic is exercised. Calls take a
    list of chunks, as the wrapper always passes one.
    """

    def __init__(
        self,
        *,
        budget: int | None = None,
        max_positions: int = 514,
        model_max_length: int = 512,
        specials: int = 2,
        drift: int = 0,
        emit: dict[str, str] | None = None,
        groups: dict[str, int] | None = None,
        id2label: dict[int, str] | None = None,
        padding_idx: int | None = None,
        embeddings: bool = True,
    ) -> None:
        self.tokenizer = FakeTokenizer(model_max_length, specials, drift, groups)
        base = SimpleNamespace()
        if embeddings:
            base.embeddings = SimpleNamespace(
                position_embeddings=SimpleNamespace(
                    num_embeddings=max_positions, padding_idx=padding_idx
                )
            )
        self.model = SimpleNamespace(
            config=SimpleNamespace(
                id2label=id2label or {0: "O", 1: "B-PER", 2: "I-PER"},
            ),
            base_model=base,
        )
        self._budget = budget if budget is not None else min(max_positions, model_max_length) - specials
        self._drift = drift
        self._emit = emit
        self.chunks: list[str] = []
        self.batch_sizes: list[int | None] = []
        self.truncated = 0

    @property
    def batch_calls(self) -> int:
        return len(self.batch_sizes)

    def __call__(self, chunks: list[str], batch_size=None):
        self.batch_sizes.append(batch_size)
        return [self._one(chunk) for chunk in chunks]

    def _count(self, text: str) -> int:
        return len(self.tokenizer.pieces(text)) + self._drift

    def _one(self, chunk: str) -> list[dict]:
        self.chunks.append(chunk)
        if self._count(chunk) > self._budget:
            self.truncated += 1
            kept: list[str] = []
            for word in chunk.split():
                if self._count(" ".join(kept + [word])) > self._budget:
                    break
                kept.append(word)
            chunk = " ".join(kept)
        if self._emit is not None:
            return self._emit_by_text(chunk)
        out = []
        first, second = NAME
        words = chunk.split()
        for m in re.finditer(r"\S+", chunk):
            if m.group() == first and chunk[m.end():m.end() + 1 + len(second)] == f" {second}":
                out.append(_item(m.start(), m.end() + 1 + len(second), "PER"))
        if words and words[-1] == first:
            start = chunk.rindex(first)
            out.append(_item(start, start + len(first), "PER"))
        if words and words[0] == second:
            out.append(_item(0, len(second), "PER"))
        return out

    def _emit_by_text(self, chunk: str) -> list[dict]:
        out = []
        for needle, label in self._emit.items():
            for m in re.finditer(re.escape(needle), chunk):
                out.append(_item(m.start(), m.end(), label))
        return out


def _item(start: int, end: int, label: str) -> dict:
    return {"entity_group": label, "score": 0.99, "start": start, "end": end}


def document(count: int, name_at: list[int] | None = None) -> tuple[str, list[tuple[int, int]]]:
    """``count`` word tokens ``w0 w1 ...``; ``NAME`` placed at each start in ``name_at``."""
    tokens = [f"w{i}" for i in range(count)]
    for start in name_at or []:
        tokens[start], tokens[start + 1] = NAME
    text = " ".join(tokens)
    offsets, pos = [], 0
    for token in tokens:
        offsets.append((pos, pos + len(token)))
        pos += len(token) + 1
    return text, offsets


class StubRegistry:
    """Hands the detector one fixed pipeline for every language."""

    def __init__(self, pipe) -> None:
        self._pipe = pipe

    def get_pipelines(self, language: str) -> list:
        return [self._pipe]
