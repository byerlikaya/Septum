"""Shared date grammar for the date recognizers.

Month names come from CLDR (Babel) for the languages of
``_MONTH_NAME_LANGUAGES``; ordinal day suffixes and date-context cues come
from mapping tables keyed by ISO 639-1 code. Everything is compiled once
(see :func:`warm_up`) and a text is scanned once by :func:`find_dates`.

Name classes:

* *strong* — wide CLDR month names of at least four letters, as written
  (``March``, ``Mart``).
* *weak* — abbreviations, diacritic-free spellings that are not also a
  strong name, and wide names shorter than four letters (``Mar``, ``marc``,
  ``May``, ``Mac``). Such short forms collide with ordinary words across
  languages ("5 may be"), so a weak name is accepted inside a full date
  with a year, when it is capitalised or dotted (``15 May``, ``Jan. 2024``,
  ``May 15``), or next to a date-context cue. A CLDR abbreviation right
  directly after a day is accepted in any case (``15 mar``, ``3 gen``)
  unless it is also a short wide name (``5 may`` still needs corroboration).
  Capitalised collisions such as "3 Mac laptops" and abbreviation
  collisions such as "3 out of 5" are masked on purpose: over-masking is
  safe, a leaked date is not.

Unit-marked numeric dates (``2024年3月15日``, ``2024년 3월 15일``) use the
year/month/day unit characters of ``_DATE_UNIT_CHARACTERS``; a month with
no year and no day (``3月``) is a month name, not a date.

Numeric day/month without a year (``3/15``, ``15.03.``) collides with
fractions, scores and clock times, so it is accepted only next to a
date-context cue; the ``.`` form also needs a trailing dot (``15.03.``)
unless the cue follows the date.
"""

from __future__ import annotations

import datetime
import functools
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, Iterator, List, Literal, Optional, Tuple, TypedDict

from babel.localedata import Alias
from babel.localedata import load as load_locale_data

from .text_utils import locale_lower

# ISO 639-1 codes whose CLDR month names feed the grammar.
_MONTH_NAME_LANGUAGES: tuple[str, ...] = (
    "af", "ar", "az", "be", "bg", "bn", "bs", "ca", "cs", "cy", "da", "de",
    "el", "en", "es", "et", "eu", "fa", "fi", "fr", "ga", "gl", "he", "hi",
    "hr", "hu", "hy", "id", "is", "it", "ka", "kk", "lt", "lv", "mk", "mr",
    "ms", "nb", "nl", "nn", "pl", "pt", "ro", "ru", "sk", "sl", "sq", "sr",
    "sv", "sw", "ta", "te", "th", "tr", "uk", "ur", "uz",
)

# Unit characters written after the year, month and day numbers of a
# numeric date, keyed by ISO 639-1.
_DATE_UNIT_CHARACTERS: Dict[str, Dict[str, tuple[str, ...]]] = {
    "zh": {"year": ("年",), "month": ("月",), "day": ("日", "号")},
    "ja": {"year": ("年",), "month": ("月",), "day": ("日",)},
    "ko": {"year": ("년",), "month": ("월",), "day": ("일",)},
}

_MONTH_NAME_CONTEXTS: tuple[str, ...] = ("format", "stand-alone")

# Ordinal markers written right after a day number, keyed by ISO 639-1.
_ORDINAL_DAY_SUFFIXES: Dict[str, tuple[str, ...]] = {
    "en": ("st", "nd", "rd", "th"),
    "fr": ("er", "re", "e", "ème"),
    "es": ("º", "°", "o"),
    "pt": ("º", "°", "o"),
    "it": ("º", "°"),
    "nl": ("e", "ste", "de"),
    "sv": (":e", ":a"),
}

# Words that mark a nearby number as a date, keyed by ISO 639-1.
# ``before`` cues sit directly in front of the date (an optional ``:``
# between), ``after`` cues directly behind it. Languages written without
# spaces between words set ``spaced`` to False so their cues need no word
# boundary. Scores and doses share their prepositions ("pain at 5/10",
# "at 2.5 mg"), so such words are left out.
# FUTURE: move to DB so new locales can be enrolled without code changes.
class _CueTable(TypedDict, total=False):
    """Cue words of one language; ``spaced`` defaults to True when absent."""

    spaced: bool
    before: Tuple[str, ...]
    after: Tuple[str, ...]


_DatePosition = Literal["before", "after"]

_DATE_CONTEXT_CUES: Dict[str, _CueTable] = {
    "en": {"before": (
        "on", "seen", "admitted", "admission", "admit", "discharged",
        "discharge", "since", "until", "till", "from", "dated", "date",
        "dos", "visit", "visited", "follow-up", "followup", "seen on",
        "surgery", "operated", "operation", "procedure", "died", "death",
        "deceased", "expired", "onset", "due", "before", "after",
        "effective", "expires", "expiry",
    )},
    "de": {"before": (
        "am", "vom", "seit", "bis", "ab", "datum", "aufnahme", "aufgenommen",
        "entlassung", "entlassen", "operation", "operiert", "verstorben",
        "tod", "besuch", "kontrolle", "beginn",
    )},
    "fr": {"before": (
        "le", "du", "depuis", "jusqu'au", "date", "admission", "admis",
        "admise", "sortie", "opéré", "opérée", "chirurgie", "décédé",
        "décédée", "décès", "visite", "consultation", "suivi", "début",
    )},
    "es": {"before": (
        "el", "del", "desde", "hasta", "fecha", "ingreso", "ingresó", "alta",
        "cirugía", "operado", "operada", "falleció", "fallecimiento",
        "defunción", "visita", "consulta", "control", "inicio",
    )},
    "pt": {"before": (
        "em", "desde", "até", "data", "internação", "internado", "internada",
        "alta", "cirurgia", "operado", "operada", "faleceu", "óbito",
        "visita", "consulta", "retorno", "início",
    )},
    "it": {"before": (
        "il", "dal", "fino", "data", "ricovero", "ricoverato", "ricoverata",
        "dimissione", "dimesso", "dimessa", "intervento", "operato",
        "operata", "deceduto", "deceduta", "decesso", "visita", "controllo",
        "esordio",
    )},
    "nl": {"before": (
        "op", "sinds", "tot", "datum", "opname", "opgenomen", "ontslag",
        "ontslagen", "operatie", "geopereerd", "overleden", "overlijden",
        "bezoek", "controle", "begin",
    )},
    "sv": {"before": (
        "den", "sedan", "till", "datum", "inskriven", "inskrivning",
        "utskriven", "utskrivning", "operation", "opererad", "avled", "död",
        "besök", "kontroll", "debut",
    )},
    "da": {"before": (
        "den", "siden", "til", "dato", "indlagt", "indlæggelse", "udskrevet",
        "udskrivelse", "operation", "opereret", "død", "døde", "besøg",
        "kontrol", "debut",
    )},
    "nb": {"before": (
        "den", "siden", "til", "dato", "innlagt", "innleggelse", "utskrevet",
        "utskrivning", "operasjon", "operert", "død", "døde", "besøk",
        "kontroll", "debut",
    )},
    "pl": {"before": (
        "dnia", "dn", "od", "do", "data", "przyjęty", "przyjęta", "przyjęcie",
        "wypisany", "wypisana", "wypis", "operacja", "operowany", "operowana",
        "zmarł", "zmarła", "zgon", "wizyta", "kontrola", "początek",
    )},
    "ru": {"before": (
        "от", "с", "до", "по", "дата", "поступил", "поступила", "поступление",
        "госпитализирован", "госпитализирована", "выписан", "выписана",
        "выписка", "операция", "оперирован", "оперирована", "умер", "умерла",
        "смерть", "визит", "осмотр", "приём", "начало",
    )},
    "tr": {
        "before": (
            "tarih", "tarihi", "giriş", "yatış", "çıkış", "taburcu", "ameliyat",
            "operasyon", "ölüm", "vefat", "kontrol", "muayene", "başlangıç",
        ),
        "after": ("tarihinde", "tarihine", "tarihinden", "tarihli"),
    },
    "ar": {
        "before": (
            "بتاريخ", "تاريخ", "في", "منذ", "حتى", "الدخول", "دخول", "الخروج",
            "خروج", "العملية", "عملية", "الوفاة", "وفاة", "توفي", "الزيارة",
            "زيارة", "المراجعة", "مراجعة", "البداية",
        ),
    },
    "zh": {
        "spaced": False,
        "before": (
            "日期", "于", "於", "入院", "出院", "手术", "手術", "死亡", "去世",
            "就诊", "就診", "复诊", "複診", "随访", "隨訪", "发病", "發病",
        ),
        "after": (
            "入院", "出院", "手术", "手術", "死亡", "去世", "就诊", "复诊", "发病",
        ),
    },
    "ja": {
        "spaced": False,
        "before": (
            "日付", "入院", "退院", "手術", "死亡", "受診", "初診", "再診", "発症",
        ),
        "after": ("入院", "退院", "手術", "死亡", "受診", "発症", "に"),
    },
    "ko": {
        "before": (
            "날짜", "입원", "퇴원", "수술", "사망", "내원", "진료", "외래", "발병",
        ),
        "after": ("입원", "퇴원", "수술", "사망", "내원", "진료", "발병"),
    },
}

# Letters that survive NFKD decomposition but have a plain ASCII
# counterpart in keyboard-typed text.
_ASCII_FOLD_TABLE: dict[int, str] = str.maketrans({"ı": "i"})

_MIN_WEAK_NAME_LENGTH = 3
_MIN_STRONG_NAME_LENGTH = 4
_COMPACT_YEAR_RANGE = (1900, 2099)
_CUE_WINDOW = 40

_SP = r"\s{1,3}"
_ABBREVIATION_DOT = re.compile(r"\.[^\S\n]{0,3}(?P<next>\w)")
_DAY = r"(?:0?[1-9]|[12]\d|3[01])"
_CONNECTOR = rf"(?:[^\W\d_]{{1,3}}{_SP})?"
_DASHES = r"[-–—]"
# Digits and letters of scripts written with spaces may not touch a
# numeric date; ideographs and other unspaced scripts may.
_NO_ALNUM_BEFORE = r"(?<!\d)(?<![A-Za-z\u00C0-\u024F\u0370-\u052F])"
_NO_ALNUM_AFTER = r"(?!\d)(?![A-Za-z\u00C0-\u024F\u0370-\u052F])"


@dataclass(frozen=True)
class _Vocabulary:
    """Compiled month-name data shared by every scan."""

    pattern: re.Pattern[str]
    strong: frozenset[str]
    abbreviations: frozenset[str]
    cue_before: re.Pattern[str]
    cue_after: re.Pattern[str]


@dataclass(frozen=True)
class _MonthNames:
    """Lower-cased CLDR month names by class (see the module docstring)."""

    strong: frozenset[str]
    weak: frozenset[str]
    abbreviation_keys: frozenset[str]


@dataclass(frozen=True)
class DateMatch:
    """A calendar-valid date found in a text."""

    start: int
    end: int


def _resolve_month_node(node: object, data: dict) -> dict:
    """Resolve a raw CLDR month-name node without mutating Babel's cache.

    Babel's ``LocaleDataDict`` writes resolved aliases back into locale
    data that is shared between locales inheriting the same root entry,
    so reading ``stand-alone`` names through ``Locale.months`` for one
    locale can leak them into the next. Resolving the raw ``Alias`` /
    ``(Alias, overrides)`` structures here keeps every locale isolated.
    """
    if isinstance(node, Alias):
        return dict(node.resolve(data))
    if isinstance(node, tuple):
        alias, overrides = node
        merged = dict(alias.resolve(data))
        merged.update(overrides)
        return merged
    return dict(node)


def _ascii_folded(name: str) -> str:
    """Return ``name`` without diacritics, or ``""`` when it is not Latin."""
    decomposed = unicodedata.normalize("NFKD", name.translate(_ASCII_FOLD_TABLE))
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return folded if folded.isascii() else ""


def _name_key(name: str) -> str:
    """Return the comparison key of a month name (case and dotted-i folded)."""
    lowered = unicodedata.normalize("NFC", re.sub(r"\s+", " ", name).lower())
    return lowered.replace("i\u0307", "i").translate(_ASCII_FOLD_TABLE)


def _locale_month_names(language: str, width: str) -> Iterator[str]:
    """Yield the digit-free month names of one locale at one CLDR width."""
    data = load_locale_data(language)
    for context in _MONTH_NAME_CONTEXTS:
        node = _resolve_month_node(data["months"][context][width], data)
        for raw in node.values():
            if not any(ch.isdigit() for ch in raw):
                yield locale_lower(raw, language).rstrip(".")


@functools.lru_cache(maxsize=1)
def _month_names() -> _MonthNames:
    """Collect the strong, weak and abbreviation month names from CLDR."""
    strong: set[str] = set()
    abbreviations: set[str] = set()
    for language in _MONTH_NAME_LANGUAGES:
        strong.update(_locale_month_names(language, "wide"))
        abbreviations.update(_locale_month_names(language, "abbreviated"))
    weak = {
        folded
        for name in strong | abbreviations
        if (folded := _ascii_folded(name)) and folded != name
    }
    short = {n for n in strong if len(n) < _MIN_STRONG_NAME_LENGTH}
    strong -= short
    strong_keys = {_name_key(n) for n in strong}
    short_keys = {_name_key(n) for n in short}
    weak |= short | abbreviations
    weak = {
        n for n in weak
        if _name_key(n) not in strong_keys and len(n) >= _MIN_WEAK_NAME_LENGTH
    }
    abbreviation_keys = {
        _name_key(n) for n in abbreviations if len(n) >= _MIN_WEAK_NAME_LENGTH
    } - strong_keys - short_keys
    return _MonthNames(
        strong=frozenset(strong),
        weak=frozenset(weak),
        abbreviation_keys=frozenset(abbreviation_keys),
    )


def _trie_alternation(words: set[str]) -> str:
    """Render ``words`` as a prefix-trie regex (greedy, longest first)."""
    trie: dict = {}
    for word in words:
        node = trie
        for ch in word:
            node = node.setdefault(ch, {})
        node[""] = {}

    def render(node: dict) -> str:
        ends_here = "" in node
        branches = [
            (_SP if ch == " " else re.escape(ch)) + render(child)
            for ch, child in sorted(node.items())
            if ch
        ]
        if not branches:
            return ""
        if len(branches) == 1 and not ends_here:
            return branches[0]
        group = "(?:" + "|".join(branches) + ")"
        return group + "?" if ends_here else group

    return render(trie)


def _cue_words(position: _DatePosition) -> Tuple[set[str], set[str]]:
    """Return ``(bounded, unbounded)`` cue words for ``before`` or ``after``.

    Cues of spaced languages need a word boundary on their outer side;
    cues of languages marked ``spaced: False`` match inside running text.
    """
    bounded: set[str] = set()
    unbounded: set[str] = set()
    for cues in _DATE_CONTEXT_CUES.values():
        target = bounded if cues.get("spaced", True) else unbounded
        target.update(w.lower() for w in cues.get(position, ()))
    return bounded, unbounded


def _anchored_cue_pattern(bounded: set[str], unbounded: set[str]) -> re.Pattern[str]:
    """Compile a cue matcher anchored at the date edge (use with ``match``)."""
    lead = r"\s{0,3}:?\s{0,3}"
    branches = []
    if bounded:
        branches.append(rf"{lead}{_trie_alternation(bounded)}(?!\w)")
    if unbounded:
        branches.append(rf"{lead}{_trie_alternation(unbounded)}")
    return re.compile("|".join(branches), re.IGNORECASE)


def _cue_patterns() -> Tuple[re.Pattern[str], re.Pattern[str]]:
    """Compile the date-context cue matchers.

    The ``before`` matcher runs on the reversed text in front of a date, so
    both matchers are anchored at the date edge and test one position
    instead of scanning the whole window.
    """
    bounded, unbounded = _cue_words("before")
    before = _anchored_cue_pattern(
        {w[::-1] for w in bounded}, {w[::-1] for w in unbounded}
    )
    return before, _anchored_cue_pattern(*_cue_words("after"))


def _unit_alternation(component: str) -> str:
    """Return the alternation of unit characters for a date component."""
    units = {u for table in _DATE_UNIT_CHARACTERS.values() for u in table[component]}
    return "|".join(re.escape(u) for u in sorted(units))


def _compile(months: str) -> re.Pattern[str]:
    """Compile the combined date grammar around a month-name alternation."""
    ordinals = sorted(
        {s for suffixes in _ORDINAL_DAY_SUFFIXES.values() for s in suffixes},
        key=lambda suffix: (-len(suffix), suffix),
    )
    day = rf"{_DAY}(?:{'|'.join(re.escape(s) for s in ordinals)})?"
    days = rf"{day}(?:\.?\s{{0,3}}{_DASHES}\s{{0,3}}{day})?"
    short_year = rf"\d{{2}}(?![\w:])(?!{_SP}(?:{months})(?!\w))"
    month_start = r"(?<!\w)(?=[^\W\d_])"
    time = (
        r"(?:[T ]\d{2}:\d{2}(?::\d{2}(?:[.,]\d{1,9})?)?"
        r"(?:Z|[+\-]\d{2}(?::?\d{2})?)?)?"
    )
    year_unit = _unit_alternation("year")
    month_unit = _unit_alternation("month")
    day_unit = _unit_alternation("day")
    return re.compile(
        r"(?=\d)(?:"
        rf"(?<!\d)(?:(?P<u_y>\d{{4}})\s{{0,2}}(?:{year_unit})\s{{0,2}})?"
        rf"(?P<u_m>\d{{1,2}})\s{{0,2}}(?:{month_unit})"
        rf"(?:\s{{0,2}}(?P<u_d>\d{{1,2}})\s{{0,2}}(?:{day_unit}))?"
        rf"|{_NO_ALNUM_BEFORE}(?<!\d\.)(?P<iso_y>\d{{4}})(?P<iso_sep>[/.\-])"
        rf"(?P<iso_m>\d{{1,2}})(?P=iso_sep)(?P<iso_d>\d{{1,2}}){time}(?!\d)(?!\.\d)"
        rf"|{_NO_ALNUM_BEFORE}(?P<cmp_y>(?:19|20)\d{{2}})(?P<cmp_m>0[1-9]|1[0-2])"
        rf"(?P<cmp_d>0[1-9]|[12]\d|3[01]){_NO_ALNUM_AFTER}"
        rf"|{_NO_ALNUM_BEFORE}(?<!\d\.)(?P<num_a>\d{{1,2}})(?P<num_sep>[/.\-])"
        r"(?P<num_b>\d{1,2})"
        r"(?P=num_sep)(?P<num_y>\d{4}|\d{2})(?!\d)(?!\.\d)"
        rf"|(?<!\w)(?=\d)(?:{days})(?:\.?{_SP}(?P<df_c>[^\W\d_]{{1,3}}{_SP})?|[-/])"
        rf"(?P<df_m>{months})\.?"
        rf"(?:(?:,?{_SP}{_CONNECTOR}|[-/])(?P<df_y>\d{{4}})(?!\w)"
        rf"|(?:,?{_SP}|[-/])(?P<df_y2>{short_year})|(?!\w))"
        rf"|{_NO_ALNUM_BEFORE}(?<!\d[./\-])(?P<dm_a>\d{{1,2}})(?P<dm_sep>[/.])"
        rf"(?P<dm_b>\d{{1,2}})(?:\.?\s{{0,3}}{_DASHES}\s{{0,3}}"
        r"(?P<dm_a2>\d{1,2})(?P=dm_sep)(?P<dm_b2>\d{1,2}))?"
        rf"(?P<dm_dot>\.)?{_NO_ALNUM_AFTER}(?![./\-]\d)"
        r")"
        rf"|{month_start}(?P<mf_m>{months})\.?"
        rf"(?:(?:{_SP}|[-/])(?:{days})"
        rf"(?:,?(?:{_SP}|[-/])(?:(?P<mf_y>\d{{4}})(?!\w)|(?P<mf_y2>{short_year}))|(?!\w))"
        rf"|(?:,?{_SP}|[-/])(?P<my_y>\d{{4}})(?!\w))",
        re.IGNORECASE | re.UNICODE,
    )


@functools.lru_cache(maxsize=1)
def _vocabulary() -> _Vocabulary:
    """Build and cache the month vocabulary, cue matchers and grammar."""
    names = _month_names()
    cue_before, cue_after = _cue_patterns()
    return _Vocabulary(
        pattern=_compile(_trie_alternation(set(names.strong | names.weak))),
        strong=frozenset(_name_key(n) for n in names.strong),
        abbreviations=names.abbreviation_keys,
        cue_before=cue_before,
        cue_after=cue_after,
    )


def warm_up() -> None:
    """Build the grammar now so the first scan does not pay for it."""
    _vocabulary()


def grammar_fingerprint() -> str:
    """Return a short hash of the compiled grammar and cue matchers.

    Changes whenever the month vocabulary (CLDR data included), cues, units
    or grammar change; ``DETECTOR_VERSION`` is pinned against it in tests.
    """
    vocab = _vocabulary()
    digest = hashlib.sha256()
    for part in (vocab.pattern, vocab.cue_before, vocab.cue_after):
        digest.update(part.pattern.encode("utf-8"))
        digest.update(b"\0")
    digest.update(",".join(sorted(vocab.strong)).encode("utf-8"))
    digest.update(b"\0")
    digest.update(",".join(sorted(vocab.abbreviations)).encode("utf-8"))
    return digest.hexdigest()[:16]


def month_name_count() -> int:
    """Return how many distinct month names (strong and weak) are known."""
    names = _month_names()
    return len(names.strong | names.weak)


def _has_cue(text: str, start: int, end: int, vocab: _Vocabulary) -> bool:
    """Return True when a date-context cue sits right before or after."""
    if vocab.cue_before.match(text[max(0, start - _CUE_WINDOW):start][::-1]):
        return True
    return vocab.cue_after.match(text, end) is not None


def _is_strong(name: str, vocab: _Vocabulary) -> bool:
    """Return True when ``name`` is a wide CLDR month name."""
    return _name_key(name) in vocab.strong


def _is_marked(m: re.Match[str], group: str, text: str) -> bool:
    """Return True when a weak month name is capitalised or abbreviation-dotted.

    A dot counts only when the text goes on in lower case or with a digit
    (``15 jan. 2024``, ``jan. 15``); a dot that ends the sentence or the
    text is punctuation, not an abbreviation mark.
    """
    name = m.group(group)
    if name[0].isupper():
        return True
    dot = _ABBREVIATION_DOT.match(text, m.end(group))
    if dot is None:
        return False
    following = dot.group("next")
    return following.isdigit() or following.islower()


def _numeric_day_month_valid(first: str, second: str) -> bool:
    """Return True when two numbers read as day/month or month/day."""
    a, b = int(first), int(second)
    return (1 <= a <= 31 and 1 <= b <= 12) or (1 <= a <= 12 and 1 <= b <= 31)


def _unit_date_end(m: re.Match[str], end: int) -> Optional[int]:
    """Validate a unit-suffixed date (year/month/day markers)."""
    if m.group("u_y") is None and m.group("u_d") is None:
        return None
    if not 1 <= int(m.group("u_m")) <= 12:
        return None
    if m.group("u_d") is not None and not 1 <= int(m.group("u_d")) <= 31:
        return None
    return end


def _iso_date_end(m: re.Match[str], end: int) -> Optional[int]:
    """Validate a year-first date with a shared separator."""
    month, day = int(m.group("iso_m")), int(m.group("iso_d"))
    return end if 1 <= month <= 12 and 1 <= day <= 31 else None


def _compact_date_end(m: re.Match[str], end: int) -> Optional[int]:
    """Validate an eight-digit ``YYYYMMDD`` date against the calendar."""
    year = int(m.group("cmp_y"))
    if not _COMPACT_YEAR_RANGE[0] <= year <= _COMPACT_YEAR_RANGE[1]:
        return None
    try:
        datetime.date(year, int(m.group("cmp_m")), int(m.group("cmp_d")))
    except ValueError:
        return None
    return end


def _numeric_date_end(m: re.Match[str], end: int) -> Optional[int]:
    """Validate a fully numeric day/month/year date."""
    first, second = m.group("num_a"), m.group("num_b")
    if (
        m.group("num_sep") == "."
        and len(m.group("num_y")) == 2
        and (len(first) < 2 or len(second) < 2)
    ):
        return None
    return end if _numeric_day_month_valid(first, second) else None


def _day_month_end(
    m: re.Match[str], text: str, vocab: _Vocabulary, end: int
) -> Optional[int]:
    """Validate a year-less numeric day/month pair (or range) that needs a cue."""
    dotted = m.group("dm_dot") is not None
    dot_separated = m.group("dm_sep") == "."
    if not _numeric_day_month_valid(m.group("dm_a"), m.group("dm_b")):
        return None
    if m.group("dm_a2") is not None and not _numeric_day_month_valid(
        m.group("dm_a2"), m.group("dm_b2")
    ):
        return None
    final_end = end - 1 if dotted and not dot_separated else end
    if dot_separated and not dotted:
        return end if vocab.cue_after.match(text, end) is not None else None
    return final_end if _has_cue(text, m.start(), final_end, vocab) else None


def _month_year_end(
    m: re.Match[str], text: str, vocab: _Vocabulary, end: int
) -> Optional[int]:
    """Validate a month name followed by a year."""
    if _is_strong(m.group("mf_m"), vocab) or _is_marked(m, "mf_m", text):
        return end
    return None


def _named_month_end(
    m: re.Match[str], text: str, vocab: _Vocabulary, end: int
) -> Optional[int]:
    """Validate a date that spells its month as a name."""
    group = "df_m" if m.group("df_m") is not None else "mf_m"
    has_year = any(
        m.group(g) is not None for g in ("df_y", "df_y2", "mf_y", "mf_y2")
    )
    final_end = end - 1 if not has_year and text[end - 1] == "." else end
    if has_year or _is_strong(m.group(group), vocab) or _is_marked(m, group, text):
        return final_end
    if (
        group == "df_m"
        and m.group("df_c") is None
        and _name_key(m.group(group)) in vocab.abbreviations
    ):
        return final_end
    return final_end if _has_cue(text, m.start(), final_end, vocab) else None


def _accepted_end(m: re.Match[str], text: str, vocab: _Vocabulary) -> Optional[int]:
    """Validate a raw match and return its final end offset, or ``None``."""
    end = m.end()
    if m.group("u_m") is not None:
        return _unit_date_end(m, end)
    if m.group("iso_y") is not None:
        return _iso_date_end(m, end)
    if m.group("cmp_y") is not None:
        return _compact_date_end(m, end)
    if m.group("num_a") is not None:
        return _numeric_date_end(m, end)
    if m.group("dm_a") is not None:
        return _day_month_end(m, text, vocab, end)
    if m.group("my_y") is not None:
        return _month_year_end(m, text, vocab, end)
    return _named_month_end(m, text, vocab, end)


def find_dates(text: str) -> List[DateMatch]:
    """Scan ``text`` once and return every calendar-valid date.

    A rejected candidate restarts the scan one character later, so a
    shorter valid date that overlaps it (``March 2024`` after a rejected
    ``set 15``) is still found.
    """
    vocab = _vocabulary()
    found: List[DateMatch] = []
    pos = 0
    length = len(text)
    while pos < length:
        m = vocab.pattern.search(text, pos)
        if m is None:
            break
        end = _accepted_end(m, text, vocab)
        if end is None:
            pos = m.start() + 1
            continue
        found.append(DateMatch(start=m.start(), end=end))
        pos = max(m.end(), m.start() + 1)
    return found


def find_date_spans(text: str) -> List[Tuple[int, int]]:
    """Return ``(start, end)`` offsets of calendar-valid dates in ``text``."""
    return [(d.start, d.end) for d in find_dates(text)]
