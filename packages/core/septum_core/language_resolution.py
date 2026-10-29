"""Language-code canonicalization and script-aware language resolution.

Statistical detectors return region- or script-qualified tags (``zh-cn``,
``zh-tw``, ``pt_BR``) while every language-keyed table in Septum is keyed
by the ISO 639-1 base code. :func:`canonical_language_code` folds a tag to
that base so lookups never miss on a subtag.

Short texts are where detectors fail: a few Han ideographs carry little
statistical signal and are often attributed to a language that writes
them only occasionally. :func:`resolve_language` checks the detector's
guess against the Unicode scripts actually present in the text and falls
back to a context language or the script-implied language when the guess
is weak or contradicted. The detector itself is injected as ranked
candidates, so this module needs no detection library.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import (
    AbstractSet,
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Optional,
    Tuple,
)

# A detector probability below this is treated as a guess, not a result.
# langdetect averages seven random trials on short input, so 0.8 demands
# that at least six of seven agree.
LOW_CONFIDENCE_THRESHOLD = 0.8

# Raw characters taken from the middle and from the end of a long text
# when an override is judged, next to its head (see ``override_sample``).
OVERRIDE_SLICE_CHARS = 10_000

# Share of a text's letters an override's scripts need before a user-chosen
# language is accepted for it: a phone label (" Tel") in Han text is not
# enough for ``de``, a few real Han names in English prose are for ``zh``.
OVERRIDE_MIN_SCRIPT_SHARE = 0.05

# Share of a text's letters a script needs before it counts as present.
# One Hangul syllable in a run of Han names is noise, not Korean: Korean is
# written mostly in Hangul. A rule may set a lower share when its script is
# sparse by nature (see ``_ScriptRule.min_share``).
MIN_SCRIPT_SHARE = 0.2

# langdetect drops URLs and e-mail addresses, then reads n-grams from the
# first 10 000 remaining characters. :func:`detection_sample` builds that
# same text so the detector and the script profile judge identical input.
DETECTION_SAMPLE_CHARS = 10_000

# Raw characters stripped before the sample is cut, so cost stays bounded
# on any document size. Ten times the sample leaves room for a URL or
# e-mail preamble well beyond the sample length to be stripped away.
DETECTION_RAW_HEAD_CHARS = 100_000

# When the head strips down to no letters at all (a link dump before the
# body), the head grows to this cap and is stripped again. The worst case
# stays one strip of this many characters.
DETECTION_RAW_CAP_CHARS = 1_000_000

_URL_RE = re.compile(r"https?://[-_.?&~;+=/#0-9A-Za-z]{1,2076}")
_WHITESPACE_RUN = re.compile(r"\s+")
_MAIL_RE = re.compile(r"[-_.0-9A-Za-z]{1,64}@[-_0-9A-Za-z]{1,255}[-_.0-9A-Za-z]{1,255}")


@dataclass(frozen=True)
class _ScriptRule:
    """A script whose presence implies, or rules out, specific languages.

    ``implied_language`` is set only when the script points to one
    language (Hangul, Greek); scripts shared by many languages (Latin,
    Cyrillic, Arabic) leave it ``None`` and only rule languages in or out.
    ``min_span_len`` is the NER span-length floor for spans written in the
    script, where a two-glyph span is often a full name.
    """

    ranges: Tuple[Tuple[int, int], ...]
    compatible_languages: FrozenSet[str]
    implied_language: Optional[str] = None
    min_share: float = MIN_SCRIPT_SHARE
    min_span_len: Optional[int] = None
    # False for scripts written without spaces between words: a word
    # boundary there falls between any two glyphs (see text_boundaries).
    inter_word_spaces: bool = True
    # True where NER recall is near zero, so an NER hit snaps to the whole
    # unspaced run around it instead of the glyph edge.
    snap_whole_run: bool = False
    # True for spaced scripts that write particles and copulas onto the
    # preceding token without a space, so a token may end right before a
    # letter of the script (see text_boundaries).
    attaches_particles: bool = False


# When several scripts are present, a script with fewer compatible
# languages is more specific (kana inside Han text decides the language).
# Add a script or a language code here; the resolution logic stays
# unchanged. Codes are ISO 639-1 (639-3 where no two-letter code exists).
_SCRIPT_RULES: Dict[str, _ScriptRule] = {
    "Hangul": _ScriptRule(
        ranges=(
            (0x1100, 0x11FF), (0x3130, 0x318F), (0xA960, 0xA97F),
            (0xAC00, 0xD7AF), (0xD7B0, 0xD7FF), (0xFFA0, 0xFFDC),
        ),
        implied_language="ko",
        compatible_languages=frozenset({"ko"}),
        min_span_len=2,
        attaches_particles=True,
    ),
    "Kana": _ScriptRule(
        ranges=(
            (0x3040, 0x309F), (0x30A0, 0x30FF), (0x31F0, 0x31FF),
            (0xFF66, 0xFF9F),
        ),
        implied_language="ja",
        compatible_languages=frozenset({"ja"}),
        # Kana carry particles and inflections between kanji, so even a
        # short Japanese phrase ("血圧計の電池交換方法") is about 10% kana.
        min_share=0.1,
        min_span_len=2,
        inter_word_spaces=False,
    ),
    "Thai": _ScriptRule(
        ranges=((0x0E00, 0x0E7F),),
        implied_language="th",
        compatible_languages=frozenset({"th"}),
        min_span_len=2,
        inter_word_spaces=False,
        snap_whole_run=True,
    ),
    "Han": _ScriptRule(
        ranges=(
            (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF),
            (0x20000, 0x2FA1F), (0x30000, 0x323AF),
        ),
        implied_language="zh",
        compatible_languages=frozenset({"zh", "ja", "ko"}),
        min_span_len=2,
        inter_word_spaces=False,
    ),
    "Greek": _ScriptRule(
        ranges=((0x0370, 0x03FF), (0x1F00, 0x1FFF)),
        implied_language="el",
        compatible_languages=frozenset({"el"}),
    ),
    "Hebrew": _ScriptRule(
        ranges=((0x0590, 0x05FF), (0xFB1D, 0xFB4F)),
        implied_language="he",
        compatible_languages=frozenset({"he", "yi"}),
    ),
    "Gurmukhi": _ScriptRule(
        ranges=((0x0A00, 0x0A7F),),
        implied_language="pa",
        compatible_languages=frozenset({"pa"}),
    ),
    "Gujarati": _ScriptRule(
        ranges=((0x0A80, 0x0AFF),),
        implied_language="gu",
        compatible_languages=frozenset({"gu"}),
    ),
    "Tamil": _ScriptRule(
        ranges=((0x0B80, 0x0BFF),),
        implied_language="ta",
        compatible_languages=frozenset({"ta"}),
    ),
    "Telugu": _ScriptRule(
        ranges=((0x0C00, 0x0C7F),),
        implied_language="te",
        compatible_languages=frozenset({"te"}),
    ),
    "Kannada": _ScriptRule(
        ranges=((0x0C80, 0x0CFF),),
        implied_language="kn",
        compatible_languages=frozenset({"kn"}),
    ),
    "Malayalam": _ScriptRule(
        ranges=((0x0D00, 0x0D7F),),
        implied_language="ml",
        compatible_languages=frozenset({"ml"}),
    ),
    "Devanagari": _ScriptRule(
        ranges=((0x0900, 0x097F), (0xA8E0, 0xA8FF)),
        compatible_languages=frozenset({"hi", "mr", "ne", "sa", "kok", "mai"}),
    ),
    "Bengali": _ScriptRule(
        ranges=((0x0980, 0x09FF),),
        compatible_languages=frozenset({"bn", "as"}),
    ),
    "Arabic": _ScriptRule(
        ranges=(
            (0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF),
            (0xFB50, 0xFDFF), (0xFE70, 0xFEFF),
        ),
        compatible_languages=frozenset({"ar", "fa", "ur", "ps", "sd", "ug", "ku"}),
    ),
    "Cyrillic": _ScriptRule(
        ranges=((0x0400, 0x04FF), (0x0500, 0x052F), (0x1C80, 0x1C8F)),
        compatible_languages=frozenset({
            "ru", "uk", "bg", "mk", "sr", "be", "kk", "ky", "mn", "tg", "tt",
            "ba", "cv", "uz", "az",
        }),
    ),
    "Latin": _ScriptRule(
        ranges=(
            (0x0041, 0x005A), (0x0061, 0x007A), (0x00C0, 0x024F),
            (0x1E00, 0x1EFF), (0xFF21, 0xFF3A), (0xFF41, 0xFF5A),
        ),
        compatible_languages=frozenset({
            "af", "az", "bs", "ca", "cs", "cy", "da", "de", "en", "eo", "es",
            "et", "eu", "fi", "fil", "fo", "fr", "fy", "ga", "gd", "gl", "ha",
            "hr", "ht", "hu", "id", "ig", "is", "it", "jv", "la", "lb", "lt",
            "lv", "mg", "mi", "ms", "mt", "nb", "nl", "nn", "no", "oc", "pl",
            "pt", "rm", "ro", "sk", "sl", "sm", "sn", "so", "sq", "sr", "st",
            "su", "sv", "sw", "tk", "tl", "tn", "tr", "uz", "vi", "wo", "xh",
            "yo", "zu", "ku",
        }),
    ),
}

_SCRIPT_PATTERNS: Dict[str, re.Pattern[str]] = {
    script: re.compile(
        "[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in rule.ranges) + "]"
    )
    for script, rule in _SCRIPT_RULES.items()
}


def _ranges_where(predicate: Callable[[_ScriptRule], bool]) -> Tuple[Tuple[int, int], ...]:
    """Code-point ranges of the table scripts whose rule satisfies ``predicate``."""
    return tuple(
        span for rule in _SCRIPT_RULES.values() if predicate(rule) for span in rule.ranges
    )


def unspaced_script_ranges() -> Tuple[Tuple[int, int], ...]:
    """Code-point ranges of the table scripts written without spaces between words."""
    return _ranges_where(lambda rule: not rule.inter_word_spaces)


def particle_script_ranges() -> Tuple[Tuple[int, int], ...]:
    """Code-point ranges of the table scripts that attach particles to the preceding token."""
    return _ranges_where(lambda rule: rule.attaches_particles)


_WHOLE_RUN_PATTERNS: Tuple[re.Pattern[str], ...] = tuple(
    _SCRIPT_PATTERNS[script]
    for script, rule in _SCRIPT_RULES.items()
    if rule.snap_whole_run
)


def snaps_whole_run(text: str) -> bool:
    """Whether ``text`` contains a script whose NER spans snap to the whole unspaced run."""
    return any(pattern.search(text) for pattern in _WHOLE_RUN_PATTERNS)


def _required_scripts() -> Dict[str, FrozenSet[str]]:
    """Scripts each language cannot be written without, derived from the rules.

    A language some script implies needs that script (``ko`` needs Hangul,
    ``ja`` needs kana); any other listed language needs one of the scripts
    it is compatible with (``ru`` needs Cyrillic).
    """
    required: Dict[str, FrozenSet[str]] = {}
    languages = set().union(*(rule.compatible_languages for rule in _SCRIPT_RULES.values()))
    for language in languages:
        implied = frozenset(
            script for script, rule in _SCRIPT_RULES.items()
            if rule.implied_language == language
        )
        required[language] = implied or frozenset(
            script for script, rule in _SCRIPT_RULES.items()
            if language in rule.compatible_languages
        )
    return required


_REQUIRED_SCRIPTS: Dict[str, FrozenSet[str]] = _required_scripts()


def canonical_language_code(code: Optional[str]) -> str:
    """Return the lowercase ISO 639-1 base of a language tag.

    ``zh-cn``, ``zh-TW`` and ``zh_Hant`` all become ``zh``; region and
    script subtags are dropped. ``None`` and blank input return ``""``.
    """
    if not code:
        return ""
    return code.strip().replace("_", "-").split("-", 1)[0].lower()


def detection_sample(text: str) -> str:
    """The bounded text language detection reads.

    The first :data:`DETECTION_RAW_HEAD_CHARS` characters, with URLs and
    e-mail addresses replaced by spaces and whitespace runs collapsed (so
    a stripped link dump does not fill the sample with blanks), cut to
    :data:`DETECTION_SAMPLE_CHARS`. A head that strips to no letters is
    retried once with :data:`DETECTION_RAW_CAP_CHARS` characters.
    Idempotent, so callers can pass the sample to both the detector and
    :func:`resolve_language`.
    """
    sample = ""
    for limit in (DETECTION_RAW_HEAD_CHARS, DETECTION_RAW_CAP_CHARS):
        head = text[:limit]
        stripped = _MAIL_RE.sub(" ", _URL_RE.sub(" ", head))
        sample = _WHITESPACE_RUN.sub(" ", stripped)[:DETECTION_SAMPLE_CHARS]
        if len(head) < limit or any(ch.isalpha() for ch in sample):
            break
    return sample


_ISO_LANGUAGE_CODE = re.compile(r"[a-z]{2,3}")


def iso_language_code(
    code: Optional[str], allowed: Optional[AbstractSet[str]] = None
) -> Optional[str]:
    """Return the canonical code of ``code`` if it has an ISO 639 shape, else ``None``.

    Only two or three ASCII letters survive, so a malformed stored value
    can never act as a language for lookups, logging or masking. With
    ``allowed``, a well-formed code outside that set is also ``None``.
    """
    canonical = canonical_language_code(code)
    if not _ISO_LANGUAGE_CODE.fullmatch(canonical):
        return None
    if allowed is not None and canonical not in allowed:
        return None
    return canonical


def language_table_codes() -> FrozenSet[str]:
    """ISO 639 codes that key septum-core's per-language tables.

    Collected from the tables themselves (NER model map, minimum NER span
    lengths, casing rules, possessive suffixes, script rules), so adding a
    language to any of them makes it a known language without a separate
    list. Tables that apply to every language at once, such as the date
    grammar's month vocabulary, are not looked up by a document's language
    and do not count. Imports are local: this module sits below those
    tables in the import graph.
    """
    from .detector import _NER_MIN_SPAN_LEN_PER_LANGUAGE
    from .ner_model_registry import NERModelRegistry
    from .text_utils import _LOCALE_CASING_RULES, _POSSESSIVE_SUFFIXES

    keys = [
        *NERModelRegistry().DEFAULT_MODEL_MAP,
        *_NER_MIN_SPAN_LEN_PER_LANGUAGE,
        *_LOCALE_CASING_RULES,
        *_POSSESSIVE_SUFFIXES,
        *(rule.implied_language for rule in _SCRIPT_RULES.values()),
    ]
    return frozenset(code for code in map(iso_language_code, keys) if code)


@dataclass(frozen=True)
class _ScriptProfile:
    """Per-script letter counts of a text, its letter total and its dominant script."""

    counts: Dict[str, int]
    letters: int
    dominant: Optional[str]

    def significant(self, script: str) -> bool:
        """Whether ``script`` holds at least its rule's ``min_share`` of the letters."""
        count = self.counts[script]
        return count > 0 and count >= _SCRIPT_RULES[script].min_share * self.letters


def _profile_of(sample: str) -> _ScriptProfile:
    """Count letters per table script in an already bounded ``sample``.

    ``dominant`` is the table script with the most letters, unless the
    letters of scripts outside the table outnumber it; a tie goes to the
    table script, and between table scripts to the more specific one.
    """
    counts = {
        script: len(sample) - len(pattern.sub("", sample))
        for script, pattern in _SCRIPT_PATTERNS.items()
    }
    letters = sum(map(str.isalpha, sample))
    other = letters - sum(counts.values())
    best = max(
        counts,
        key=lambda script: (counts[script], -len(_SCRIPT_RULES[script].compatible_languages)),
    )
    if counts[best] == 0 or counts[best] < other:
        return _ScriptProfile(counts=counts, letters=letters, dominant=None)
    return _ScriptProfile(counts=counts, letters=letters, dominant=best)


def _deciding_script(profile: _ScriptProfile) -> Optional[str]:
    """The most specific script present within the dominant script's languages.

    Candidates are the dominant script and every significant script whose
    languages are a subset of the dominant's; fewest languages wins, then
    the higher letter count. ``None`` when no table script dominates.
    """
    if profile.dominant is None:
        return None
    family = _SCRIPT_RULES[profile.dominant].compatible_languages
    candidates = [profile.dominant] + [
        script for script, rule in _SCRIPT_RULES.items()
        if script != profile.dominant
        and profile.significant(script)
        and rule.compatible_languages <= family
    ]
    return min(
        candidates,
        key=lambda script: (
            len(_SCRIPT_RULES[script].compatible_languages),
            -profile.counts[script],
        ),
    )


def _script_allows(profile: _ScriptProfile, language: str, deciding: Optional[str]) -> bool:
    """Whether the scripts in the text are consistent with ``language``.

    A language whose own script is not significant is refused. Beyond
    that, only a deciding script that points to one language (Hangul,
    kana, Han) can refuse it: kana-heavy text is not ``zh`` even with Han
    present. A script shared by many languages (Latin, Cyrillic) never
    refuses a language whose own script is significant, so Russian with
    English technical terms stays ``ru``. Text with no letters at all
    (digits, URLs) rules nothing out. ``deciding`` is
    ``_deciding_script(profile)``, computed once by the caller.
    """
    if profile.letters == 0:
        return True
    required = _REQUIRED_SCRIPTS.get(language)
    if required and not any(profile.significant(script) for script in required):
        return False
    if deciding is None:
        return True
    rule = _SCRIPT_RULES[deciding]
    if rule.implied_language is None and required:
        return True
    return language in rule.compatible_languages


def override_sample(text: str, ignore: Optional[re.Pattern[str]] = None) -> str:
    """A bounded sample of the head, middle and tail of ``text`` for judging an override.

    A text up to :data:`DETECTION_SAMPLE_CHARS` is used whole. A longer one
    contributes its head plus :data:`OVERRIDE_SLICE_CHARS` raw characters
    from the middle and from the end, so a document whose body is in
    another script than its preamble is judged on both. The head is read
    like :func:`detection_sample` reads text: the first
    :data:`DETECTION_RAW_HEAD_CHARS`, widened (up to
    ``DETECTION_RAW_CAP_CHARS - 2 * OVERRIDE_SLICE_CHARS``) only when that
    strips to no letters, so at most :data:`DETECTION_RAW_CAP_CHARS`
    characters are read. Matches of ``ignore`` (placeholder tokens) are
    blanked in each piece after it is cut.
    """

    def _sample(piece: str) -> str:
        if ignore is not None:
            piece = ignore.sub(lambda match: " " * len(match.group()), piece)
        return detection_sample(piece)

    if len(text) <= DETECTION_SAMPLE_CHARS:
        return _sample(text)
    head = _sample(text[:DETECTION_RAW_HEAD_CHARS])
    if len(text) > DETECTION_RAW_HEAD_CHARS and not any(ch.isalpha() for ch in head):
        head = _sample(text[: DETECTION_RAW_CAP_CHARS - 2 * OVERRIDE_SLICE_CHARS])
    middle = len(text) // 2 - OVERRIDE_SLICE_CHARS // 2
    return " ".join((
        head,
        _sample(text[middle : middle + OVERRIDE_SLICE_CHARS]),
        _sample(text[-OVERRIDE_SLICE_CHARS:]),
    ))


def script_permits_override(
    text: str, language: str, ignore: Optional[re.Pattern[str]] = None
) -> bool:
    """Whether a user-chosen ``language`` is plausible for ``text`` on script grounds.

    Looser than the check :func:`resolve_language` applies to a detector's
    guess: an override is refused only when the scripts the language is
    written in make up less than :data:`OVERRIDE_MIN_SCRIPT_SHARE` of the
    letters of :func:`override_sample` (``ru`` on Arabic text). A language
    the table does not know cannot be refused, and text with no letters
    refuses nothing.
    """
    profile = _profile_of(override_sample(text, ignore))
    required = _REQUIRED_SCRIPTS.get(canonical_language_code(language))
    if profile.letters == 0 or not required:
        return True
    share = sum(profile.counts[script] for script in required) / profile.letters
    return share >= OVERRIDE_MIN_SCRIPT_SHARE


def _span_floor_patterns() -> Tuple[Tuple[int, re.Pattern[str]], ...]:
    """One pattern per span floor over the scripts that set it, smallest floor first."""
    ranges_by_floor: Dict[int, List[Tuple[int, int]]] = {}
    for rule in _SCRIPT_RULES.values():
        if rule.min_span_len is not None:
            ranges_by_floor.setdefault(rule.min_span_len, []).extend(rule.ranges)
    return tuple(
        (floor, re.compile("[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in ranges) + "]"))
        for floor, ranges in sorted(ranges_by_floor.items())
    )


_SPAN_FLOOR_PATTERNS = _span_floor_patterns()


def script_min_span_length(text: str) -> Optional[int]:
    """The smallest NER span floor among the scripts present in ``text``, if any sets one."""
    if text.isascii():
        return None
    for floor, pattern in _SPAN_FLOOR_PATTERNS:
        if pattern.search(text):
            return floor
    return None


def resolve_language(
    sample: str,
    candidates: Iterable[Tuple[str, float]],
    *,
    context_language: Optional[str] = None,
    default: str = "en",
) -> str:
    """Pick the ISO 639-1 language of ``sample`` from ranked detector candidates.

    ``sample`` is :func:`detection_sample` of the text, the same string the
    detector read; it is profiled as given.

    Candidate probabilities are summed per canonical code, so ``zh-cn`` and
    ``zh-tw`` support one ``zh``. The top candidate wins when its
    probability reaches :data:`LOW_CONFIDENCE_THRESHOLD` and the text's
    scripts allow it. Otherwise the first of these that the scripts allow
    is used: ``context_language`` (e.g. the language of the document a
    query is asked against), the script-implied language, the top
    candidate itself, then ``default``. A ``context_language`` without an
    ISO 639 shape is ignored; an invalid ``default`` raises ``ValueError``.
    """
    totals: Dict[str, float] = {}
    for code, probability in candidates:
        canonical = iso_language_code(code)
        if canonical:
            totals[canonical] = totals.get(canonical, 0.0) + probability

    fallback = iso_language_code(default)
    if fallback is None:
        raise ValueError("default must be an ISO 639 language code")
    profile = _profile_of(sample)
    deciding = _deciding_script(profile)
    top = max(totals, key=lambda code: totals[code]) if totals else None
    top_allowed = top is not None and _script_allows(profile, top, deciding)
    if top_allowed and totals[top] >= LOW_CONFIDENCE_THRESHOLD:
        return top

    context = iso_language_code(context_language)
    if context and _script_allows(profile, context, deciding):
        return context
    implied = _SCRIPT_RULES[deciding].implied_language if deciding is not None else None
    if implied:
        return implied
    if top_allowed:
        return top
    return fallback
