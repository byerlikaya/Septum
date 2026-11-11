from __future__ import annotations

"""
Recognizer registry for septum-core.

Builds Presidio recognizers from:
- Built-in regulation packs (for example GDPR, HIPAA, KVKK).
- User-defined :class:`CustomRecognizerLike` records passed in by the
  caller.

The core registry is intentionally network-free: custom recognizers
that use ``detection_method='llm_prompt'`` are built through an
optional ``llm_recognizer_factory`` callback injected by the caller.
If no factory is supplied, LLM-backed custom recognizers are skipped
with a warning. This keeps all network/LLM plumbing outside the
air-gapped core.
"""

import functools
import importlib
import logging
import re
import unicodedata
from array import array
from bisect import bisect_left, bisect_right
from itertools import accumulate
from typing import Callable, List, Optional, Sequence

from presidio_analyzer import (
    EntityRecognizer,
    Pattern,
    PatternRecognizer,
)

from ..regulations.models import CustomRecognizerLike, RegulationRulesetLike
from ..text_boundaries import TOKEN_END, TOKEN_START, token_bounded

logger = logging.getLogger(__name__)

LLMRecognizerFactory = Callable[[CustomRecognizerLike], Optional[EntityRecognizer]]

CONTEXT_WINDOW_CHARS = 100
"""Characters on each side of a match searched for a rule's context words."""

_EXTRA_FOLDS = str.maketrans({"\u0131": "i"})
"""Letters that case folding leaves apart from their plain form."""


@functools.lru_cache(maxsize=65536)
def _fold_char(ch: str) -> str:
    """Fold one character: whitespace to a space, case and diacritics removed."""
    if ch.isspace():
        return " "
    decomposed = unicodedata.normalize("NFKD", ch)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.casefold().translate(_EXTRA_FOLDS)


def _fold_table(text: str) -> dict[int, str]:
    """Translation table folding every distinct character of ``text``."""
    return {ord(ch): _fold_char(ch) for ch in set(text)}


def _fold_for_context(word: str) -> str:
    """Folded form of a context word: trimmed, each whitespace run one space."""
    return " ".join(word.translate(_fold_table(word)).split())


def _same_index(index: int) -> int:
    """Origin of a folded index when every character folds to exactly one."""
    return index


def _origin_lookup(text: str, table: dict[int, str]) -> Callable[[int], int]:
    """Map an index of ``text.translate(table)`` to its source character's index."""
    if all(len(folded) == 1 for folded in table.values()):
        return _same_index
    ends = array("q", accumulate(map(len, map(table.__getitem__, map(ord, text)))))
    return functools.partial(bisect_right, ends)


_WORD_CHAR = re.compile(r"\w")


def _context_word_body(word: str) -> str:
    """Regex for a folded context word without its start edge.

    A space in the word matches any run of whitespace. An end that is a word
    character must sit at a token boundary, so "ID" is not found in "IDEA";
    an end that is punctuation ("No:") already separates the word from its
    neighbour, so it matches literally.
    """
    end = TOKEN_END if _WORD_CHAR.match(word[-1]) else ""
    return " +".join(re.escape(part) for part in word.split(" ")) + end


def _compile_context_regex(words: set[str]) -> Optional[re.Pattern[str]]:
    """One overlapping-search regex for folded context words; ``None`` for none.

    Group 1 of each match is the longest word starting at that position.
    Words starting with a word character must start at a token boundary,
    checked once for all of them, so each alternative opens with a literal
    the regex engine can rule out cheaply; words starting with punctuation
    ("#id", "(ref)") match their start literally. The two kinds never start
    at the same position. A leading first-character class lets most
    positions fail before any alternative is tried.
    """
    if not words:
        return None
    ordered = sorted(words, key=lambda w: (-len(w), w))
    bounded = [_context_word_body(w) for w in ordered if _WORD_CHAR.match(w[0])]
    literal = [_context_word_body(w) for w in ordered if not _WORD_CHAR.match(w[0])]
    branches = []
    if bounded:
        branches.append(f"{TOKEN_START}(?:{'|'.join(bounded)})")
    if literal:
        branches.append(f"(?:{'|'.join(literal)})")
    first_chars = "".join(re.escape(c) for c in sorted({w[0] for w in words}))
    return re.compile(f"(?=[{first_chars}])(?=({'|'.join(branches)}))")


class ContextGatedPatternRecognizer(PatternRecognizer):
    """Pattern recognizer that keeps a match only when a context word is near it.

    The window of a match runs from :data:`CONTEXT_WINDOW_CHARS` characters
    before its start to :data:`CONTEXT_WINDOW_CHARS` characters after its
    end, the match included. A match is kept when a context word occurs in
    the original text as a whole token (ignoring case, diacritics and
    whitespace runs; an edge of the word that is punctuation matches as is)
    and at least one of its characters lies in the window. The word is
    judged on the full text, not on the window: a word cut by the window
    edge ("XID" seen as "ID") does not count, and one that crosses the edge
    does. With no context words the recognizer behaves like
    :class:`PatternRecognizer`.
    """

    def __init__(self, *args, context_words: Sequence[str] = (), **kwargs) -> None:
        super().__init__(*args, **kwargs)
        folded = {_fold_for_context(w) for w in context_words if w}
        folded.discard("")
        self._context_regex = _compile_context_regex(folded)

    def without_gate(self) -> PatternRecognizer:
        """The same patterns as a plain recognizer that keeps every match."""
        return PatternRecognizer(
            supported_entity=self.supported_entities[0],
            patterns=self.patterns,
            name=self.name,
            supported_language=self.supported_language,
        )

    @staticmethod
    def _context_hits(
        context_regex: re.Pattern[str], text: str
    ) -> tuple[array, array]:
        """Locate every context word of ``text`` in one pass over its folded form.

        Returns the source index of each kept hit's first character, ascending,
        and for each kept hit the furthest source index of a last character
        among it and the hits before it. A hit's last character includes the
        characters folded to nothing after it (a trailing combining mark). A
        hit that does not extend that furthest index is not kept: an earlier
        hit, starting no later, already reaches as far, so no window query
        changes.
        """
        table = _fold_table(text)
        origin = _origin_lookup(text, table)
        firsts = array("q")
        reach = array("q")
        furthest = -1
        for match in context_regex.finditer(text.translate(table)):
            end = match.end(1)
            last = max(origin(end - 1), origin(end) - 1)
            if last > furthest:
                furthest = last
                firsts.append(origin(match.start(1)))
                reach.append(furthest)
        return firsts, reach

    def analyze(self, text, entities, nlp_artifacts=None, regex_flags=None):
        """Run the pattern match, then drop results without a nearby context word."""
        results = super().analyze(text, entities, nlp_artifacts, regex_flags)
        context_regex = self._context_regex
        if context_regex is None or not results:
            return results
        firsts, reach = self._context_hits(context_regex, text)

        def has_context(start: int, end: int) -> bool:
            high = min(len(text), end + CONTEXT_WINDOW_CHARS)
            starting_before = bisect_left(firsts, high)
            low = max(0, start - CONTEXT_WINDOW_CHARS)
            return starting_before > 0 and reach[starting_before - 1] >= low

        return [r for r in results if has_context(r.start, r.end)]


def _recognizer_dedup_key(recognizer: EntityRecognizer):
    """Return a hashable content key, or ``None`` if the recognizer is not
    safely deduplicable (e.g. carries opaque callables / state).

    Two recognizers sharing this key produce identical detections for the
    same input — keeping only the first one is a pure runtime win.
    """
    try:
        entities = tuple(sorted(recognizer.supported_entities))
        patterns = getattr(recognizer, "patterns", None) or ()
        pattern_key = tuple(
            sorted((p.regex, p.score) for p in patterns)
        )
        deny_list = tuple(sorted(getattr(recognizer, "deny_list", None) or ()))
        context = tuple(sorted(getattr(recognizer, "context", None) or ()))
    except Exception:  # pragma: no cover - defensive
        return None
    return (
        type(recognizer).__name__,
        entities,
        pattern_key,
        deny_list,
        context,
    )


def _admit_unseen(recognizer: EntityRecognizer, seen_keys: set) -> bool:
    """Record ``recognizer``'s dedup key and report whether it is new.

    A recognizer without a usable key is always admitted.
    """
    key = _recognizer_dedup_key(recognizer)
    if key is None:
        return True
    if key in seen_keys:
        return False
    seen_keys.add(key)
    return True


def _load_pack_recognizers(
    base_pkg: str, regulation_id: str
) -> Optional[List[EntityRecognizer]]:
    """Return a regulation pack's recognizers, or ``None`` if it cannot be loaded."""
    module_path = f"{base_pkg}.{regulation_id}.recognizers"
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError:
        logger.debug(
            "No recognizer pack found for regulation '%s' (module %s).",
            regulation_id,
            module_path,
        )
        return None

    get_recognizers = getattr(module, "get_recognizers", None)
    if get_recognizers is None:
        logger.warning(
            "Recognizer pack %s for regulation '%s' "
            "does not define get_recognizers().",
            module_path,
            regulation_id,
        )
        return None

    try:
        return list(get_recognizers())
    except Exception as exc:
        logger.error(
            "Error while loading recognizers from pack %s: %s",
            module_path,
            type(exc).__name__,
        )
        return None


def has_context_words(custom: CustomRecognizerLike) -> bool:
    """Whether a custom rule has a context word that is not blank."""
    return any(w and w.strip() for w in custom.context_words or ())


def _pattern_recognizer(
    custom: CustomRecognizerLike, pattern: Pattern
) -> PatternRecognizer:
    """Build ``pattern``'s recognizer, context-gated when the rule has context words."""
    options = {
        "supported_entity": custom.entity_type,
        "patterns": [pattern],
        "name": custom.name,
        "supported_language": "en",
    }
    if has_context_words(custom):
        return ContextGatedPatternRecognizer(
            context_words=custom.context_words, **options
        )
    return PatternRecognizer(**options)


class RecognizerRegistry:
    """
    Build Presidio recognizers for the active regulations and custom rules.

    The registry is deliberately stateless; callers pass in the active
    regulation rulesets and custom recognizers, and the registry returns
    a flat list of :class:`EntityRecognizer` instances.

    An optional ``llm_recognizer_factory`` lets hosts (backend,
    MCP server, tests) inject a concrete LLM-backed recognizer
    implementation for custom rules whose ``detection_method`` is
    ``'llm_prompt'``. When omitted, such rules are skipped so the
    core package never attempts an outbound network call on its own.
    """

    def __init__(
        self,
        llm_recognizer_factory: Optional[LLMRecognizerFactory] = None,
    ) -> None:
        self._llm_recognizer_factory = llm_recognizer_factory

    def build(
        self,
        active_regs: Sequence[RegulationRulesetLike],
        custom_recognizers: Sequence[CustomRecognizerLike],
    ) -> List[EntityRecognizer]:
        """Return all recognizers for the given regulations and custom rules."""
        recognizers: List[EntityRecognizer] = []
        recognizers.extend(self._load_builtin_packs(active_regs))
        recognizers.extend(self._from_custom_recognizers(custom_recognizers))
        return recognizers

    def _load_builtin_packs(
        self,
        active_regs: Sequence[RegulationRulesetLike],
    ) -> List[EntityRecognizer]:
        """Load recognizers from built-in packs with cross-pack deduplication.

        Many regulations ship structurally identical recognizers for common
        entity types (several declare the same employee-ID recognizer). Without dedup, Presidio
        evaluates N near-identical regexes on every call — pure runtime
        waste. We collapse duplicates by a stable content key (class name,
        entity types, pattern regex+score set, deny list, context words).
        First occurrence wins; subsequent duplicates are dropped.
        """
        recognizers: List[EntityRecognizer] = []
        seen_keys: set = set()
        base_pkg = __name__.rsplit(".", 1)[0]
        for reg in active_regs:
            pack_recognizers = _load_pack_recognizers(base_pkg, reg.id)
            if pack_recognizers is None:
                continue
            recognizers.extend(
                r for r in pack_recognizers if _admit_unseen(r, seen_keys)
            )

        return recognizers

    def _from_custom_recognizers(
        self,
        custom_recognizers: Sequence[CustomRecognizerLike],
    ) -> List[EntityRecognizer]:
        """Build Presidio recognizers from custom recognizer models."""
        recognizers: List[EntityRecognizer] = []
        for custom in custom_recognizers:
            if not custom.is_active:
                continue
            recognizer = self._build_custom_recognizer(custom)
            if recognizer is not None:
                recognizers.append(recognizer)
        return recognizers

    def _build_custom_recognizer(
        self,
        custom: CustomRecognizerLike,
    ) -> Optional[EntityRecognizer]:
        """Build one active custom recognizer, or ``None`` when it is skipped."""
        method = custom.detection_method.lower()
        if method == "regex" and custom.pattern:
            return self._build_regex_recognizer(custom)
        if method == "keyword_list" and custom.keywords:
            return self._build_keyword_recognizer(custom)
        if method == "llm_prompt" and custom.llm_prompt:
            return self._build_llm_recognizer(custom)
        logger.warning(
            "Skipping custom recognizer %s (%s) due to incomplete configuration.",
            custom.id,
            custom.name,
        )
        return None

    @staticmethod
    def _build_regex_recognizer(
        custom: CustomRecognizerLike,
    ) -> Optional[PatternRecognizer]:
        """Build a PatternRecognizer from a regex-based custom recognizer."""
        try:
            compiled = re.compile(custom.pattern or "")
        except re.error:
            logger.warning(
                "Invalid regex pattern for custom recognizer %s (%s); skipping.",
                custom.id,
                custom.name,
            )
            return None

        pattern = Pattern(
            name=custom.name,
            regex=compiled.pattern,
            score=0.6,
        )
        return _pattern_recognizer(custom, pattern)

    @staticmethod
    def _build_keyword_recognizer(
        custom: CustomRecognizerLike,
    ) -> PatternRecognizer:
        """Build a PatternRecognizer from a keyword-list custom recognizer."""
        keywords: List[str] = list(custom.keywords or [])
        escaped = [re.escape(k) for k in keywords if k]
        if not escaped:
            raise ValueError("Keyword recognizer requires at least one keyword.")

        alternation = "|".join(escaped)
        pattern = Pattern(
            name=f"{custom.name}_keywords",
            # The leading lookahead keeps the edge assertions off every
            # position that does not start a keyword. A keyword that starts or
            # ends in punctuation (".NET", "C++") keeps the old ``\b``
            # behaviour at that end.
            regex=f"(?=(?:{alternation})){token_bounded(alternation)}",
            score=0.7,
        )
        return _pattern_recognizer(custom, pattern)

    def _build_llm_recognizer(
        self,
        custom: CustomRecognizerLike,
    ) -> Optional[EntityRecognizer]:
        """Delegate LLM-backed custom recognizer construction to the host factory.

        When no factory is injected (the default for air-gapped hosts),
        the custom rule is skipped and a warning is logged.
        """
        if self._llm_recognizer_factory is None:
            logger.warning(
                "Custom recognizer %s (%s) uses detection_method='llm_prompt' but "
                "no llm_recognizer_factory is attached to the registry; skipping.",
                custom.id,
                custom.name,
            )
            return None
        try:
            return self._llm_recognizer_factory(custom)
        except Exception as exc:
            logger.error(
                "LLM recognizer factory failed for custom rule %s (%s); skipping: %s",
                custom.id,
                custom.name,
                type(exc).__name__,
            )
            return None
