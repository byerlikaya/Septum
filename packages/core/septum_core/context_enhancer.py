"""Linear-time replacement for Presidio's ``LemmaContextAwareEnhancer`` context lookup.

For every analyzer result, Presidio's enhancer finds the result's token by
scanning the token list from the start and collects the nearby context words
by testing each lemma against the keyword *list*. Both scans repeat per result,
so a text with a match every few words costs time quadratic in its length
(400K digit-dense characters spent most of a minute here).

:class:`LinearContextEnhancer` builds, once per ``enhance_using_context`` call,
the running maximum of the token end offsets and the positions of the lemmas
that are keywords, then answers each result with two binary searches. It
returns exactly what Presidio's ``_extract_surrounding_words`` returns (same
words, same order, same ``ValueError`` when no token covers the start), and
leaves scoring and every default to the Presidio base class.

The index lives in a :class:`~contextvars.ContextVar` for the duration of one
call, never on the enhancer: one enhancer serves every thread that shares the
detector. Called outside ``enhance_using_context``, the lookup builds its own.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from contextvars import ContextVar
from typing import Dict, List, Optional

from presidio_analyzer import EntityRecognizer, RecognizerResult
from presidio_analyzer.context_aware_enhancers import LemmaContextAwareEnhancer
from presidio_analyzer.nlp_engine import NlpArtifacts


class _TokenIndex:
    """Lookup tables over one call's NLP artifacts."""

    __slots__ = ("_lemma_count", "_reach", "_first_at", "_keyword_positions", "_keyword_words")

    def __init__(self, artifacts: NlpArtifacts) -> None:
        self._lemma_count = len(artifacts.lemmas)
        starts = artifacts.tokens_indices
        reach: List[int] = []
        first_at: Dict[int, int] = {}
        furthest: Optional[int] = None
        for i, token in enumerate(artifacts.tokens):
            start = starts[i]
            end = start + len(token)
            if furthest is None or end > furthest:
                furthest = end
            reach.append(furthest)
            first_at.setdefault(start, i)
        self._reach = reach
        self._first_at = first_at

        keywords = set(artifacts.keywords)
        positions: List[int] = []
        words: List[str] = []
        for i, lemma in enumerate(artifacts.lemmas):
            lowered = lemma.lower()
            if lowered in keywords:
                positions.append(i)
                words.append(lowered)
        self._keyword_positions = positions
        self._keyword_words = words

    def token_at(self, word: str, start: int) -> int:
        """First token that starts at ``start`` or ends after it, as Presidio picks it."""
        covering = bisect_right(self._reach, start)
        exact = self._first_at.get(start)
        if exact is not None and exact < covering:
            return exact
        if covering < len(self._reach):
            return covering
        raise ValueError(
            "Did not find word '" + word + "' "
            "in the list of tokens although it "
            "is expected to be found"
        )

    def _take(self, index: int, n_words: int) -> int:
        """How many words Presidio collects from ``index``: none when it starts outside the lemmas."""
        return n_words + 1 if 0 <= index < self._lemma_count else 0

    def backward(self, index: int, n_words: int) -> List[str]:
        """Keyword lemmas at or before ``index``, nearest first, at most ``n_words + 1``."""
        take = self._take(index, n_words)
        if take <= 0:
            return []
        stop = bisect_right(self._keyword_positions, index)
        return self._keyword_words[max(0, stop - take) : stop][::-1]

    def forward(self, index: int, n_words: int) -> List[str]:
        """Keyword lemmas at or after ``index``, nearest first, at most ``n_words + 1``."""
        take = self._take(index, n_words)
        if take <= 0:
            return []
        begin = bisect_left(self._keyword_positions, index)
        return self._keyword_words[begin : begin + take]


_CALL_INDEX: ContextVar[Optional[_TokenIndex]] = ContextVar(
    "septum_context_enhancer_index", default=None
)


class LinearContextEnhancer(LemmaContextAwareEnhancer):
    """``LemmaContextAwareEnhancer`` whose per-result context lookup is logarithmic."""

    def enhance_using_context(
        self,
        text: str,
        raw_results: List[RecognizerResult],
        nlp_artifacts: NlpArtifacts,
        recognizers: List[EntityRecognizer],
        context: Optional[List[str]] = None,
    ) -> List[RecognizerResult]:
        """Run Presidio's enhancement with one index built for this call's artifacts."""
        index = _TokenIndex(nlp_artifacts) if nlp_artifacts is not None else None
        token = _CALL_INDEX.set(index)
        try:
            return super().enhance_using_context(
                text=text,
                raw_results=raw_results,
                nlp_artifacts=nlp_artifacts,
                recognizers=recognizers,
                context=context,
            )
        finally:
            _CALL_INDEX.reset(token)

    def _extract_surrounding_words(
        self, nlp_artifacts: NlpArtifacts, word: str, start: int
    ) -> List[str]:
        """Return Presidio's context words for the token at ``start``, via the call's index."""
        if not nlp_artifacts.tokens:
            return [""]
        index = _CALL_INDEX.get()
        if index is None:
            index = _TokenIndex(nlp_artifacts)
        token_index = index.token_at(word, start)
        context_list = index.backward(token_index, self.context_prefix_count)
        context_list.extend(index.forward(token_index, self.context_suffix_count))
        return list(set(context_list))
