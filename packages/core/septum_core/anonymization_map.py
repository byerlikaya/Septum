"""Anonymization map: original PII values and their placeholders.

The map records which placeholder (``[ENTITY_TYPE_N]``) stands for each
original value, plus a token-to-placeholder table so residual mentions
of a person's name are redacted with the same placeholder. Placeholder
reuse (coreference) is limited to person-identifying types; every other
value only shares a placeholder with an identical value of the same type.

The map holds raw PII. This package never writes it anywhere: it lives
in memory, and a host that keeps it across requests is responsible for
storing it encrypted (the Septum API keeps per-document maps in memory
and writes them AES-256-GCM encrypted to Redis and to disk). It must
never be sent to the frontend, to a cloud LLM, or to logs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import partial
from itertools import combinations
from typing import (
    AbstractSet,
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Mapping,
    NamedTuple,
    Optional,
    Sequence,
    Set,
    Tuple,
    cast,
)

from .text_utils import (
    APOSTROPHE_SUFFIX_TAILS,
    APOSTROPHES,
    normalize_for_comparison,
    normalize_unicode,
    strip_possessive_suffix,
)

# A placeholder this map hands out: ``[ENTITY_TYPE_N]``. Index digits are
# capped: real indexes are small, and an unbounded run of digits from
# untrusted text would make ``int()`` raise.
PLACEHOLDER_PATTERN = re.compile(r"\[([A-Z][A-Z_]*)_(\d{1,9})\]", re.ASCII)
# Any ``[TYPE_N]`` a map hands out, custom entity types outside
# ``PLACEHOLDER_PATTERN`` included ("[MRN2_1]", "[Project code_1]").
PLACEHOLDER_SHAPE = re.compile(r"\[([^\[\]\n]{1,64})_(\d{1,9})\]")
# Short forms an LLM writes for a placeholder ("[PERSON_1]" for
# "[PERSON_NAME_1]"); the unmasker restores them like the full form.
PLACEHOLDER_SHORT_ALIASES: Dict[str, str] = {
    "PERSON_NAME": "PERSON",
    "ORGANIZATION_NAME": "ORGANIZATION",
}
_ALIAS_FULL_FORMS: Dict[str, str] = {short: full for full, short in PLACEHOLDER_SHORT_ALIASES.items()}

# FUTURE: migrate to NonPiiRule table for full user-editability.
# Minimal stopwords kept here for technical filtering (ultra-common function words).
SANITIZER_STOPWORDS: frozenset[str] = frozenset({"the", "a", "an", "of", "in", "at"})

# Entity types whose tokens should be propagated through the blocklist for
# coreference resolution.  Only person-identifying types qualify; other
# categories (locations, identifiers, …) would cause excessive collateral
# replacements of common words.
BLOCKLIST_ENTITY_TYPES: frozenset[str] = frozenset({
    "PERSON_NAME", "FIRST_NAME", "LAST_NAME", "ALIAS", "USERNAME",
})

# Hyphen variants that join two parts of one name ("Jean-Pierre"). The en dash
# is left out: between names it reads as "and", not as one name.
_HYPHENS: frozenset[str] = frozenset({"-", "‐", "‑"})
# Every hyphen variant folds to "-" and every apostrophe to "'", one character
# to one character, so "O’Brien" and "O'Brien" share a blocklist key.
_JOINER_FOLD = str.maketrans({**dict.fromkeys(_HYPHENS, "-"), **dict.fromkeys(APOSTROPHES, "'")})
_JOINERS = "".join(re.escape(ch) for ch in sorted(_HYPHENS | APOSTROPHES))
HAS_JOINER = re.compile(rf"[{_JOINERS}]")
# A joiner between two word runs, kept by ``split`` so a run can be rebuilt.
_JOINER_SPLIT = re.compile(rf"([{_JOINERS}])")
# A word character that is not a joiner. U+02BC (ʼ) is a Unicode letter, so
# ``\w`` alone would read "OʼBrien" as one plain word.
_WORD_CHAR = rf"[^\W{_JOINERS}]"
# Text tokens for ``apply_blocklist``: a placeholder, or word runs joined by
# hyphens/apostrophes ("O'Brien", "Jean-Pierre", "Kaya’nın") so a joined name
# is looked up whole before its parts. Group 1 is non-empty for a joined run.
_TEXT_TOKEN = re.compile(
    rf"\[[^\]]+\]|{_WORD_CHAR}+((?:[{_JOINERS}]{_WORD_CHAR}+)*)", re.UNICODE
)
_WORD = re.compile(rf"{_WORD_CHAR}+", re.UNICODE)
# Upper bound on the parts of one joined run tried as a single name, so a long
# run of joined words in untrusted text stays linear.
_MAX_JOINED_PARTS = 6
# Most tokens of a name whose subsets are enumerated to find shorter names it
# contains; longer names fall back to scanning every holder of their tokens.
_MAX_SUBSET_TOKENS = 8
_EMPTY: FrozenSet[str] = frozenset()


@dataclass
class AnonymizationMap:
    """In-memory anonymization map with language-aware coreference support.

    The map tracks original entity strings and the placeholder assigned to
    them, plus a token-to-placeholder mapping used when applying the blocklist:
    residual token mentions are replaced with the correct [ENTITY_TYPE_N]
    placeholder, never with a generic [BLOCKED]. Tokens in SANITIZER_STOPWORDS
    are never added to the blocklist and are never redacted.
    """

    document_id: int
    language: str = "en"
    entity_map: Dict[str, str] = field(default_factory=dict)
    blocklist: Set[str] = field(default_factory=set)
    token_to_placeholder: Dict[str, str] = field(default_factory=dict)
    token_counter: Dict[str, int] = field(default_factory=dict)
    # Optional placeholder→original lookup populated only by the chat-time
    # multi-document unification path. ``entity_map`` is keyed by original
    # so a single original cannot carry two distinct placeholders even
    # though the unification can legitimately assign two of them when the
    # same name was detected as different entity types across documents
    # (e.g. ``Antalya`` as LOCATION in one doc and ORGANIZATION_NAME in
    # another). The reversed lookup keeps every (placeholder, original)
    # pair so the deanonymizer can still resolve every placeholder the
    # cloud LLM might echo. Per-document maps leave this field empty and
    # the deanonymizer derives the lookup from ``entity_map`` directly.
    # When it is set, placeholders this map mints for shared name parts are
    # mirrored into it.
    placeholder_lookup: Dict[str, str] = field(default_factory=dict)
    # Lookup indexes derived from ``entity_map``; never persisted. They are
    # rebuilt whenever ``entity_map`` changed size behind ``add_entity``
    # (callers that fill the map directly, deserialized maps).
    _value_index: Dict[Tuple[str, str], str] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )
    _person: "_PersonIndex" = field(init=False, repr=False, compare=False)
    _indexed_size: int = field(default=-1, init=False, repr=False, compare=False)
    # Placeholders in use, for ``_next_placeholder``; rebuilt with the index.
    _used: Optional[Set[str]] = field(default=None, init=False, repr=False, compare=False)
    # Most parts of a joined token key, and the key count it was taken at.
    _joined_window: int = field(default=1, init=False, repr=False, compare=False)
    _window_size: int = field(default=-1, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Reconstruct blocklist and lookup indexes when deserialized.

        Token keys are folded once (see ``_JOINER_FOLD``), so a map persisted
        before keys were folded ("o’brien") matches text in any variant, and a
        joined key gains the part keys a new registration would add ("brien"
        from "o'brien"). A part key another placeholder already owns keeps its
        owner: it is a token shared by two people. Maps without a joined key
        skip both steps.
        """
        derived: List[str] = []
        if _has_joined_key(self.token_to_placeholder):
            derived = self._fold_and_derive_keys()
        if _has_joined_key(self.blocklist):
            self.blocklist = {token.translate(_JOINER_FOLD) for token in self.blocklist}
        if self.token_to_placeholder and not self.blocklist:
            self.blocklist = set(self.token_to_placeholder.keys())
        else:
            self.blocklist.update(derived)
        self._sync_index()
        self._resolve_shared_keys()

    def _resolve_shared_keys(self) -> None:
        """Give every token key shared by two person placeholders a neutral owner.

        Maps saved before shared tokens were detected can still point a
        shared token ("smith" of John and Jane Smith) at one person. The live
        rule (``_shared_token_placeholder``) is applied once on load; a map
        saved by this version is left as it is.
        """
        table = self.token_to_placeholder
        person = self._person
        for token in person.shared_tokens():
            owner = table.get(token)
            if owner is None or _placeholder_entity_type(owner) not in BLOCKLIST_ENTITY_TYPES:
                continue
            if person.bare_group(owner, frozenset((token,)))[1]:
                continue
            original = person.sample.get(token)
            if original is not None:
                entity_type = _placeholder_entity_type(owner)
                table[token] = self._shared_token_placeholder(original, token, entity_type, owner)

    def _fold_and_derive_keys(self) -> List[str]:
        """Fold joined token keys and add their missing part keys; return the added keys.

        Works on a copy, so the caller's dict (a loaded payload) is left as is.
        """
        table = self.token_to_placeholder = dict(self.token_to_placeholder)
        joined = [key for key in table if HAS_JOINER.search(key)]
        for key in joined:
            folded = key.translate(_JOINER_FOLD)
            if folded != key:
                table.setdefault(folded, table.pop(key))
        added: List[str] = []
        for key in dict.fromkeys(key.translate(_JOINER_FOLD) for key in joined):
            for part in _blocklistable(_name_keys(key, self.language)):
                if part not in table:
                    table[part] = table[key]
                    added.append(part)
        return added

    def add_entity(self, original: str, entity_type: str) -> str:
        """Register an entity span and return the placeholder for it.

        Coreference resolution is applied so that if ``original`` can be
        matched to a previously seen value (for example, "John" after
        "John Smith"), the existing placeholder is reused; see
        ``_find_existing`` for when a match is allowed.

        Only person-identifying entity types (see ``BLOCKLIST_ENTITY_TYPES``)
        have their tokens propagated to the blocklist. Other entity types are
        recorded in the entity map but do not trigger blocklist expansion,
        which avoids collateral replacement of common words (e.g. city names).
        """
        if not original:
            raise ValueError("original must be a non-empty string.")
        if not entity_type:
            raise ValueError("entity_type must be a non-empty string.")

        placeholder = self._find_existing(original, entity_type)
        if placeholder is None:
            placeholder = self._next_placeholder(entity_type)
        if original not in self.entity_map:
            self.entity_map[original] = placeholder
            self._index_entry(original, placeholder)
        if entity_type in BLOCKLIST_ENTITY_TYPES:
            self._update_blocklist(original, placeholder, entity_type)
        return placeholder

    def adopt_many(
        self,
        originals: Iterable[str],
        placeholder: str,
        entity_type: str,
        normalized: Optional[Mapping[str, str]] = None,
    ) -> None:
        """Register ``originals`` under a placeholder chosen by the caller.

        Used to build one placeholder space out of several maps: no
        coreference is attempted, the caller has already decided which
        placeholder the originals belong to. An original the map already
        holds keeps its entry (the map is keyed by original); its tokens are
        still registered for ``placeholder``, so a token two placeholders
        share moves to a neutral one (see ``_shared_token_placeholder``).
        Each original is normalized once (``normalized`` may carry the
        comparison forms of joiner-free originals the caller already has),
        and the tokens of originals with the same blocklist keys are
        registered once.
        """
        self._sync_index()
        self._used_placeholders().add(placeholder)
        person = entity_type in BLOCKLIST_ENTITY_TYPES
        registered: Set[Tuple[str, ...]] = set()
        for original in originals:
            form = normalized.get(original) if normalized else None
            if form is None and not HAS_JOINER.search(original):
                form = normalize_for_comparison(original, self.language)
            if original not in self.entity_map:
                self.entity_map[original] = placeholder
                self._index_entry(original, placeholder, form)
            if not person:
                continue
            keys = tuple(_name_keys(original, self.language, form))
            if keys not in registered:
                registered.add(keys)
                self._update_blocklist(original, placeholder, entity_type, keys, form)

    def inert(self, placeholder: str) -> str:
        """A placeholder for a stray ``placeholder`` that must restore to nothing.

        A stray is a placeholder found in text that no entry of the map
        stands behind. It is kept as written when this map uses neither it
        nor the form it aliases with (see ``PLACEHOLDER_SHORT_ALIASES``),
        and both are reserved so the map never hands them out; otherwise a
        new unused placeholder of its type, whose alias is unused too,
        replaces it, its alias reserved likewise. Neither gets an entry.
        """
        self._sync_index()
        used = self._used_placeholders()
        match = PLACEHOLDER_SHAPE.fullmatch(placeholder)
        if match is None:
            used.add(placeholder)
            return placeholder
        if not self._alias_taken(placeholder):
            for form in (placeholder, _alias_partner(placeholder)):
                if form is not None:
                    self._reserve(form)
            return placeholder
        renamed = self._next_placeholder(match.group(1))
        while self._alias_taken(renamed, own=False):
            renamed = self._next_placeholder(match.group(1))
        partner = _alias_partner(renamed)
        if partner is not None:
            self._reserve(partner)
        return renamed

    def _alias_taken(self, placeholder: str, own: bool = True) -> bool:
        """Whether ``placeholder`` (when ``own``) or the form it aliases with is in use.

        A short form ("[PERSON_1]") restores like its full form, so either
        one in use takes both.
        """
        used = self._used_placeholders()
        partner = _alias_partner(placeholder)
        return (own and placeholder in used) or (partner is not None and partner in used)

    def _reserve(self, placeholder: str) -> None:
        """Mark ``placeholder`` used and raise its type's counter to its index."""
        self._used_placeholders().add(placeholder)
        match = PLACEHOLDER_SHAPE.fullmatch(placeholder)
        if match is not None:
            entity_type, index = match.group(1), int(match.group(2))
            if self.token_counter.get(entity_type, 0) < index:
                self.token_counter[entity_type] = index

    def mint(self, entity_type: str) -> str:
        """Return a new placeholder of ``entity_type`` no entry of this map uses."""
        self._sync_index()
        return self._next_placeholder(entity_type)

    def bare_only_holder(self, token: str, entity_type: str) -> Optional[str]:
        """The person placeholder of ``entity_type`` that holds nothing but bare ``token``.

        When several do (a redundant neutral), the lowest index wins, so the
        answer does not depend on set order.
        """
        self._sync_index()
        key = frozenset((token,))
        found = [
            placeholder
            for placeholder in _named(self._person.by_set, key)
            if _placeholder_entity_type(placeholder) == entity_type
            and self._person.bare_group(placeholder, key)[1]
        ]
        return min(found, key=placeholder_order) if found else None

    def known_value(
        self, original: str, entity_type: str, normalized: Optional[str] = None
    ) -> Optional[str]:
        """The placeholder of an equal value (normalized, possessive stripped) of ``entity_type``.

        ``normalized`` may carry the comparison form of a joiner-free ``original``.
        """
        self._sync_index()
        if normalized is None or HAS_JOINER.search(original):
            normalized = self._normalized(original)
        return self._value_index.get((entity_type, normalized))

    def person_group_keys(self, placeholder: str) -> List[FrozenSet[str]]:
        """The whole-token sets of the groups of person ``placeholder``, in ``entity_map`` order.

        A set holds the comparison tokens longer than two characters.
        """
        self._sync_index()
        return list(self._person.groups.get(placeholder, ()))

    def coreferent_person(self, original: str) -> Optional[str]:
        """The one person placeholder ``original`` refers to (``_PersonIndex.match``), or None."""
        self._sync_index()
        forms = _coreference_forms(original, self.language)
        if not forms.whole:
            return None
        return self._person.match(forms, self.language)

    def apply_blocklist(self, text: str, language: Optional[str] = None) -> str:
        """Apply token-level redaction using token_to_placeholder.

        Tokens that match a known entity token are replaced with the
        corresponding [ENTITY_TYPE_N] placeholder. Placeholder format only.
        SANITIZER_STOPWORDS are never redacted. Each distinct token is looked
        up once per call.
        """
        if not text or not self.token_to_placeholder:
            return text
        redact = partial(self._redact_match, language or self.language, {})
        return _TEXT_TOKEN.sub(redact, text)

    def _redact_match(self, language: str, seen: Dict[str, str], match: re.Match[str]) -> str:
        """Redaction of one ``_TEXT_TOKEN`` match, memoised in ``seen``."""
        run = match.group(0)
        replacement = seen.get(run)
        if replacement is None:
            if match.group(1):
                replacement = self._redact_joined(run, language)
            elif run.startswith("["):
                replacement = run
            else:
                replacement = self._redact_word(run, language)
            seen[run] = replacement
        return replacement

    def _redact_word(self, word: str, language: str) -> str:
        """Placeholder of a known single word, else the word as written."""
        normalized = normalize_for_comparison(word, language)
        if normalized in SANITIZER_STOPWORDS:
            return word
        return self.token_to_placeholder.get(normalized, word)

    def _redact_joined(self, run: str, language: str) -> str:
        """Redact words joined by hyphens or apostrophes.

        Joined words are matched greedily from the left, longest known name
        first: "O'Brien's" becomes "[P]'s", "Jean-Pierre" one placeholder, and
        "Smith-Jones" with only "Smith" known "[P]-Jones". Joiners between
        unmatched parts are kept as written. A map without joined keys looks
        each part up alone.
        """
        pieces = _JOINER_SPLIT.split(run)
        words, joiners = pieces[0::2], pieces[1::2]
        window = self._window()
        out: List[str] = []
        start = 0
        while start < len(words):
            end, replacement = self._longest_known(words, joiners, start, window, language)
            out.append(replacement)
            if end < len(words):
                out.append(joiners[end - 1])
            start = end
        return "".join(out)

    def _longest_known(
        self, words: List[str], joiners: List[str], start: int, window: int, language: str
    ) -> Tuple[int, str]:
        """Longest known name starting at ``words[start]``: (end index, replacement)."""
        for end in range(min(len(words), start + window), start + 1, -1):
            candidate = words[start]
            for index in range(start + 1, end):
                candidate += joiners[index - 1] + words[index]
            placeholder = self._joined_placeholder(candidate, language)
            if placeholder is not None:
                return end, placeholder
        return start + 1, self._redact_word(words[start], language)

    def _joined_placeholder(self, token: str, language: str) -> Optional[str]:
        """Placeholder of a known joined token, folded or as written."""
        normalized = normalize_for_comparison(token, language)
        placeholder = self.token_to_placeholder.get(normalized.translate(_JOINER_FOLD))
        if placeholder is None:
            placeholder = self.token_to_placeholder.get(normalized)
        return placeholder

    def _window(self) -> int:
        """Most parts of any joined token key, capped at ``_MAX_JOINED_PARTS``.

        Recomputed only when the key count moved behind ``_update_blocklist``
        (pronoun registration, map merges, loading).
        """
        if self._window_size != len(self.token_to_placeholder):
            self._joined_window = 1
            for key in self.token_to_placeholder:
                self._widen_window(key)
            self._window_size = len(self.token_to_placeholder)
        return self._joined_window

    def _widen_window(self, key: str) -> None:
        """Raise the joined-key window to cover ``key``."""
        if HAS_JOINER.search(key):
            parts = len(HAS_JOINER.findall(key)) + 1
            self._joined_window = min(_MAX_JOINED_PARTS, max(self._joined_window, parts))

    def _next_placeholder(self, entity_type: str) -> str:
        """Return the next placeholder of ``entity_type`` that is not in use yet.

        A stale or missing ``token_counter`` (an old or merged map) never
        hands out a placeholder the map already uses in ``entity_map``,
        ``token_to_placeholder`` or ``placeholder_lookup``.
        """
        used = self._used_placeholders()
        count = self.token_counter.get(entity_type, 0) + 1
        placeholder = f"[{entity_type}_{count}]"
        while placeholder in used:
            count += 1
            placeholder = f"[{entity_type}_{count}]"
        self.token_counter[entity_type] = count
        used.add(placeholder)
        return placeholder

    def _used_placeholders(self) -> Set[str]:
        """Placeholders in use, collected once and kept up to date by ``_next_placeholder``."""
        if self._used is None:
            self._used = {
                *self.entity_map.values(),
                *self.token_to_placeholder.values(),
                *self.placeholder_lookup,
            }
        return self._used

    def _update_blocklist(
        self,
        original: str,
        placeholder: str,
        entity_type: str,
        keys: Optional[Sequence[str]] = None,
        normalized: Optional[str] = None,
    ) -> None:
        """Update blocklist and token_to_placeholder from ``original``.

        All tokens from detected entities are added to blocklist regardless of
        casing, except very short tokens (<=2 chars) and stopwords. This ensures
        residual mentions of person names (e.g. lowercase "sarah", "david") are
        redacted by apply_blocklist even when NER models miss them.

        Tokens come from ``_name_keys``, so ``"Ahmet's"`` and ``"Ahmet"``
        produce the same blocklist tokens, and a joined name adds its parts.

        A token another placeholder already owns is shared by two people
        ("Ali" of "Ali Yılmaz" and "Ali Kaya"). It then maps to a neutral
        placeholder that restores to the token alone (see
        ``_shared_token_placeholder``), so it stays masked without naming
        either person. ``keys`` and ``normalized`` may carry the name keys
        and the comparison form of a joiner-free ``original`` when the
        caller already has them.
        """
        self._window()
        table = self.token_to_placeholder
        if keys is None:
            keys = _name_keys(original, self.language)
        for token in _blocklistable(keys):
            self.blocklist.add(token)
            owner = table.get(token)
            if owner is not None and owner != placeholder:
                owner = self._shared_token_placeholder(
                    original, token, entity_type, owner, normalized
                )
            table[token] = owner or placeholder
            if normalized is None:
                self._widen_window(token)
        self._window_size = len(table)

    def _shared_token_placeholder(
        self,
        original: str,
        token: str,
        entity_type: str,
        owner: str,
        normalized: Optional[str] = None,
    ) -> str:
        """Neutral placeholder of the bare form of a shared ``token``.

        The bare form is the token as written in ``original``. A placeholder
        that holds nothing but bare forms of the token already is neutral and
        is kept, so no placeholder loses its last original. Otherwise a new
        placeholder is minted and the bare forms move to it ("Smith" seen
        while John Smith was the only Smith); the old placeholder keeps
        restoring through its fuller originals. A bare form registered as a
        non-person entity keeps that placeholder.
        """
        surface, exact = _surface_form(original, token, self.language, normalized)
        holder = self.entity_map.get(surface, owner)
        if _placeholder_entity_type(holder) not in BLOCKLIST_ENTITY_TYPES:
            return holder
        if exact:
            bare_key = frozenset((token,)) if len(token) > 2 else _EMPTY
        else:
            bare_key = frozenset(_coreference_forms(surface, self.language).whole)
        has_bare, only_bare = self._person.bare_group(holder, bare_key)
        if only_bare:
            neutral = holder
        else:
            neutral = self._next_placeholder(entity_type)
            if has_bare:
                self._move_group(bare_key, holder, neutral)
        if surface not in self.entity_map:
            self.entity_map[surface] = neutral
            self._index_entry(surface, neutral, token if exact else None)
        if self.placeholder_lookup:
            self.placeholder_lookup.setdefault(neutral, surface)
        return neutral

    def _move_group(self, key: FrozenSet[str], holder: str, neutral: str) -> None:
        """Move the bare forms of ``holder`` with token set ``key`` to ``neutral``.

        Only the moved originals are touched. Every token key of a moved
        original that still points at ``holder`` ("smith's" of a moved
        "SMITH’S") moves with it, so a suffixed bare mention no longer
        restores to the first person.
        """
        originals = self._person.move(holder, neutral, key)
        holder_type = _placeholder_entity_type(holder)
        neutral_type = _placeholder_entity_type(neutral)
        for original in originals:
            self.entity_map[original] = neutral
            for token in _name_keys(original, self.language):
                if self.token_to_placeholder.get(token) == holder:
                    self.token_to_placeholder[token] = neutral
            normalized = self._normalized(original)
            if self._value_index.get((holder_type, normalized)) == holder:
                del self._value_index[(holder_type, normalized)]
            self._value_index.setdefault((neutral_type, normalized), neutral)
        if self.placeholder_lookup:
            self.placeholder_lookup[neutral] = pick_restoration_original(originals)
            if self.placeholder_lookup.get(holder) in originals:
                remaining = self._person.originals_of(holder)
                if remaining:
                    self.placeholder_lookup[holder] = pick_restoration_original(remaining)

    def _normalized(self, value: str) -> str:
        """Return the comparison form of ``value`` (possessive stripped)."""
        stripped = strip_possessive_suffix(value, self.language)
        return normalize_for_comparison(stripped, self.language)

    def _index_entry(
        self, original: str, placeholder: str, normalized: Optional[str] = None
    ) -> None:
        """Add one ``entity_map`` entry to the lookup indexes.

        ``normalized`` may carry the comparison form of a joiner-free
        ``original`` when the caller already has it.
        """
        entity_type = _placeholder_entity_type(placeholder)
        joined = normalized is None and HAS_JOINER.search(original) is not None
        if joined:
            normalized = self._normalized(original)
        elif normalized is None:
            # No apostrophe, so no possessive to strip: one normalization serves both.
            normalized = normalize_for_comparison(original, self.language)
        self._value_index.setdefault((entity_type, normalized), placeholder)
        if entity_type in BLOCKLIST_ENTITY_TYPES:
            if joined:
                forms = _coreference_forms(original, self.language)
            else:
                forms = _plain_coref(normalized)
            self._person.add(placeholder, original, forms)
        # Only one entry was added: the index stays in sync only if it was.
        if self._indexed_size == len(self.entity_map) - 1:
            self._indexed_size = len(self.entity_map)

    def _sync_index(self) -> None:
        """Rebuild the lookup indexes when ``entity_map`` changed directly."""
        if self._indexed_size == len(self.entity_map):
            return
        self._value_index = {}
        self._person = _PersonIndex()
        self._used = None
        for original, placeholder in self.entity_map.items():
            self._index_entry(original, placeholder)
        self._indexed_size = len(self.entity_map)

    def _find_existing(self, original: str, entity_type: str) -> Optional[str]:
        """Try to find an existing placeholder for ``original``.

        An existing placeholder is reused when:

        * the original string is already a key of the map (the map is
          keyed by original, so a second placeholder for the same string
          would leave the first one unrestorable);
        * the normalized value equals an existing one of the same entity
          type (constant-time index lookup);
        * both values are person-identifying (``BLOCKLIST_ENTITY_TYPES``)
          and exactly one person placeholder matches its tokens (see
          ``_PersonIndex.match``), so ``"John"`` after ``"John Smith"`` keeps
          the same placeholder. Candidates come from a token index, so the
          lookup does not scan the map.

        Any other overlap (dates sharing a month and year, a location
        inside an address, …) gets its own placeholder, because reusing
        it would restore the wrong value on de-anonymization. The entity
        type of an existing entry is read from its placeholder, so maps
        persisted before this rule need no migration.

        Possessive suffixes are stripped before comparison so that
        ``"Ahmet's"`` matches a previously seen ``"Ahmet"``.
        """
        self._sync_index()
        if not self.entity_map:
            return None
        existing_placeholder = self.entity_map.get(original)
        if existing_placeholder is not None:
            return existing_placeholder

        normalized = self._normalized(original)
        exact = self._value_index.get((entity_type, normalized))
        if exact is not None:
            return exact
        if entity_type not in BLOCKLIST_ENTITY_TYPES:
            return None
        forms = _coreference_forms(original, self.language)
        if not forms.whole:
            return None
        return self._person.match(forms, self.language)


class _PersonIndex:
    """Name tokens of every person placeholder, indexed for lookup without a scan.

    Originals are grouped per placeholder by their whole-token set, so the
    bare forms of a token are one group and move as one; ``group_parts``
    keeps the joined parts of a group that has any. ``tokens`` and ``parts``
    hold, per placeholder, every whole token (a joined token such as
    "jean-pierre" stays one token) and joined part of its groups, with
    ``counts`` of the groups holding each once a placeholder has a second
    group. ``by_token``, ``by_part`` and ``by_set`` invert them (``by_set``
    only for short names, the only ones looked up by set). ``sample`` keeps
    the first original seen with each token. Everything is updated in place,
    also when a group moves. A single-group placeholder, the common case,
    shares its frozen key as its token set and keeps no counts, so a loaded
    map allocates little.
    """

    def __init__(self) -> None:
        self.tokens: Dict[str, AbstractSet[str]] = {}
        self.parts: Dict[str, AbstractSet[str]] = {}
        self.token_set: Dict[str, FrozenSet[str]] = {}
        self.groups: Dict[str, Dict[FrozenSet[str], List[str]]] = {}
        self.group_parts: Dict[Tuple[str, FrozenSet[str]], Set[str]] = {}
        self.counts: Dict[str, Dict[str, int]] = {}
        self.by_token: Dict[str, Set[str]] = {}
        self.by_part: Dict[str, Set[str]] = {}
        # A lone placeholder is stored as itself, more than one as a set.
        self.by_set: Dict[FrozenSet[str], object] = {}
        self.sample: Dict[str, str] = {}

    def add(self, placeholder: str, original: str, forms: "_Coref") -> None:
        """Record one person original registered under ``placeholder``."""
        key = frozenset(forms.whole)
        groups = self.groups.get(placeholder)
        if groups is None:
            self.groups[placeholder] = {key: [original]}
            self._first_group(placeholder, key, forms.parts)
        elif key in groups:
            groups[key].append(original)
        else:
            groups[key] = [original]
            self._count(placeholder, key, forms.parts, 1)
        if forms.parts:
            self.group_parts.setdefault((placeholder, key), set()).update(forms.parts)
        sample = self.sample
        for token in key:
            if token not in sample:
                sample[token] = original
        for part in forms.parts:
            if part not in sample:
                sample[part] = original

    def _first_group(self, placeholder: str, key: FrozenSet[str], parts: Set[str]) -> None:
        """Index the first group of a new placeholder (the common, fast case)."""
        if not key:
            return
        self.tokens[placeholder] = key
        for token in key:
            _add_to(self.by_token, token, placeholder)
        if len(key) <= _MAX_SUBSET_TOKENS:
            self.token_set[placeholder] = key
            _add_named(self.by_set, key, placeholder)
        if parts:
            self.parts[placeholder] = frozenset(parts)
            for part in parts:
                _add_to(self.by_part, part, placeholder)

    def move(self, holder: str, neutral: str, key: FrozenSet[str]) -> List[str]:
        """Move the group of ``holder`` with whole-token set ``key`` to ``neutral``."""
        groups = self.groups[holder]
        originals = groups.pop(key)
        if not groups:
            del self.groups[holder]
        parts = self.group_parts.pop((holder, key), set())
        self._count(holder, key, parts, -1)
        target = self.groups.get(neutral)
        if target is None:
            self.groups[neutral] = {key: originals}
            self._first_group(neutral, key, parts)
        elif key in target:
            target[key].extend(originals)
        else:
            target[key] = originals
            self._count(neutral, key, parts, 1)
        if parts:
            self.group_parts.setdefault((neutral, key), set()).update(parts)
        return list(originals)

    def _count(self, placeholder: str, key: FrozenSet[str], parts: Set[str], step: int) -> None:
        """Add (``step`` 1) or remove (-1) one group's tokens and parts."""
        counts = self.counts.get(placeholder)
        if counts is None:
            # Still a single group: each of its tokens and parts counts once.
            counts = self.counts[placeholder] = dict.fromkeys(self.tokens.get(placeholder, ()), 1)
            counts.update(dict.fromkeys(("\0" + part for part in self.parts.get(placeholder, ())), 1))
        changed = False
        for token in key:
            changed |= self._step(counts, token, placeholder, step, self.tokens, self.by_token)
        for part in parts:
            self._step(counts, "\0" + part, placeholder, step, self.parts, self.by_part, part)
        if changed:
            self._reset_set_key(placeholder)

    @staticmethod
    def _step(
        counts: Dict[str, int],
        counter: str,
        placeholder: str,
        step: int,
        per_placeholder: Dict[str, Set[str]],
        inverted: Dict[str, Set[str]],
        token: Optional[str] = None,
    ) -> bool:
        """Move one count by ``step``; return whether the token appeared or vanished."""
        token = counter if token is None else token
        count = counts.get(counter, 0) + step
        if count > 0:
            counts[counter] = count
            if count == 1 and step > 0:
                _mutable(per_placeholder, placeholder).add(token)
                _add_to(inverted, token, placeholder)
                return True
            return False
        counts.pop(counter, None)
        _mutable(per_placeholder, placeholder).discard(token)
        _discard(inverted, token, placeholder)
        return True

    def _reset_set_key(self, placeholder: str) -> None:
        """Re-index ``placeholder`` under its current token set."""
        old = self.token_set.pop(placeholder, None)
        if old is not None:
            _discard_named(self.by_set, old, placeholder)
        known = self.tokens.get(placeholder)
        if known and len(known) <= _MAX_SUBSET_TOKENS:
            key = self.token_set[placeholder] = frozenset(known)
            _add_named(self.by_set, key, placeholder)

    def originals_of(self, placeholder: str) -> List[str]:
        """Every original registered under ``placeholder``, group by group."""
        return [
            original for members in self.groups.get(placeholder, {}).values() for original in members
        ]

    def bare_group(self, placeholder: str, key: FrozenSet[str]) -> Tuple[bool, bool]:
        """(has a group with ``key``, holds nothing else) for ``placeholder``."""
        groups = self.groups.get(placeholder, {})
        return key in groups, len(groups) == 1 and key in groups

    def holders(self, token: str) -> AbstractSet[str]:
        """Placeholders whose name has ``token`` as a whole token or a part (read only)."""
        whole = self.by_token.get(token, _EMPTY)
        part = self.by_part.get(token)
        return whole | part if part else whole

    def shared_tokens(self) -> List[str]:
        """Tokens held by two or more placeholders, as a whole token or a part."""
        shared = [token for token, bucket in self.by_token.items() if len(bucket) > 1]
        shared_parts = [part for part in self.by_part if len(self.holders(part)) > 1]
        return list(dict.fromkeys(shared + shared_parts))

    def match(self, forms: "_Coref", language: str) -> Optional[str]:
        """The one person placeholder ``forms`` refer to, or None when unsure.

        A single plain token matches every placeholder that has it as a whole
        token or as a part ("Jean" for "Jean-Pierre Dupont"). Other names
        compare whole tokens, so "Jean Dupont" is not "Jean-Pierre Dupont";
        a spaced run equal to a joined token in the same order ("Jean
        Pierre", not "Pierre Jean") reads as that token. They match a
        placeholder when they are part of its name, or when its whole name is
        part of them and that name is not itself part of another person's.
        Identical token sets win outright. Two or more matches are
        ambiguous: the caller mints a new placeholder rather than merging two
        people into one.
        """
        tokens = forms.whole
        if len(tokens) == 1:
            (token,) = tokens
            if not HAS_JOINER.search(token):
                return self._match_bare(token)
        same: List[str] = []
        matches: List[str] = []
        for placeholder in self._candidates(forms):
            relation = self._relation(forms, placeholder, language)
            if relation == "same":
                same.append(placeholder)
            if relation:
                matches.append(placeholder)
        if len(same) == 1:
            return same[0]
        return matches[0] if len(matches) == 1 else None

    def _match_bare(self, token: str) -> Optional[str]:
        """The one placeholder a single plain token names, or None."""
        same = _named(self.by_set, frozenset((token,)))
        if len(same) == 1:
            return next(iter(same))
        holders = self.holders(token)
        return next(iter(holders)) if len(holders) == 1 else None

    def _candidates(self, forms: "_Coref") -> AbstractSet[str]:
        """Placeholders that can relate to ``forms``.

        Plain names look up the placeholders holding every token and the ones
        whose whole name is a subset, without touching the rest of a shared
        surname's bucket. Joined tokens, or tokens that are parts of a known
        joined name, fall back to every holder of any token.
        """
        tokens = forms.whole
        joined = len(tokens) > _MAX_SUBSET_TOKENS or any(
            HAS_JOINER.search(token) or token in self.by_part for token in tokens
        )
        if joined:
            return self._any_holders(tokens)
        return self._all_holders(tokens) | self._subset_names(tokens)

    def _all_holders(self, tokens: Set[str]) -> Set[str]:
        """Placeholders whose name holds every one of ``tokens``."""
        buckets = sorted((self.by_token.get(token, _EMPTY) for token in tokens), key=len)
        found = set(buckets[0])
        for bucket in buckets[1:]:
            if not found:
                break
            found &= bucket
        return found

    def _subset_names(self, tokens: Set[str]) -> Set[str]:
        """Placeholders whose whole name is a proper subset of ``tokens``."""
        found: Set[str] = set()
        ordered = sorted(tokens)
        for size in range(1, len(ordered)):
            for subset in combinations(ordered, size):
                found |= _named(self.by_set, frozenset(subset))
        return found

    def _any_holders(self, tokens: Set[str]) -> Set[str]:
        """Placeholders sharing a token, or a part of a joined token, with ``tokens``."""
        found: Set[str] = set()
        for token in tokens:
            found |= self.holders(token)
            if HAS_JOINER.search(token):
                for part in _split_parts(token):
                    found |= self.by_token.get(part, _EMPTY)
        return found

    def _relation(self, forms: "_Coref", placeholder: str, language: str) -> str:
        """How ``forms`` relate to ``placeholder``'s name: "same", "part" or ""."""
        known = self.tokens.get(placeholder, set())
        query = _fuse_query(forms, known)
        name = _fuse_known(known, partial(self._sequences, placeholder, language), forms.whole)
        if query == name:
            return "same"
        if query <= name:
            return "part"
        if name < query and not self._is_name_part(placeholder, known):
            return "part"
        return ""

    def _sequences(self, placeholder: str, language: str) -> List[Tuple[str, ...]]:
        """Every original's tokens in written order, for ``placeholder``."""
        return [
            _coreference_forms(original, language).ordered
            for original in self.originals_of(placeholder)
        ]

    def _is_name_part(self, placeholder: str, tokens: Set[str]) -> bool:
        """Whether ``tokens`` are part of another person placeholder's name."""
        buckets = sorted((self.holders(token) for token in tokens), key=len)
        if not buckets:
            return False
        if len(buckets) == 1:
            return len(buckets[0]) > (placeholder in buckets[0])
        others = set(buckets[0])
        others.discard(placeholder)
        for bucket in buckets[1:]:
            if not others:
                break
            others &= bucket
        return bool(others)


def _add_to(index: Dict, key: object, placeholder: str) -> None:
    """Add ``placeholder`` to the set at ``index[key]``, creating it when missing."""
    bucket = index.get(key)
    if bucket is None:
        index[key] = {placeholder}
    else:
        bucket.add(placeholder)


def _add_named(index: Dict[FrozenSet[str], object], key: FrozenSet[str], placeholder: str) -> None:
    """Add ``placeholder`` under ``key``: alone as a string, two or more as a set."""
    current = index.get(key)
    if current is None:
        index[key] = placeholder
    elif isinstance(current, set):
        current.add(placeholder)
    elif current != placeholder:
        index[key] = {current, placeholder}


def _discard_named(index: Dict[FrozenSet[str], object], key: FrozenSet[str], placeholder: str) -> None:
    """Remove ``placeholder`` from under ``key`` (see ``_add_named``)."""
    current = index.get(key)
    if current == placeholder:
        del index[key]
    elif isinstance(current, set):
        current.discard(placeholder)
        if len(current) == 1:
            index[key] = next(iter(current))


def _named(index: Dict[FrozenSet[str], object], key: FrozenSet[str]) -> AbstractSet[str]:
    """Placeholders stored under ``key`` (see ``_add_named``), as a set."""
    current = index.get(key)
    if current is None:
        return _EMPTY
    if isinstance(current, str):
        return frozenset((current,))
    return cast(Set[str], current)


def _mutable(per_placeholder: Dict[str, AbstractSet[str]], placeholder: str) -> Set[str]:
    """The placeholder's set in ``per_placeholder``, thawed from a shared frozen key if needed."""
    current = per_placeholder.get(placeholder)
    if isinstance(current, set):
        return current
    thawed = per_placeholder[placeholder] = set(current or ())
    return thawed


def _discard(index: Dict, key: object, placeholder: str) -> None:
    """Remove ``placeholder`` from ``index[key]``, dropping the key when empty."""
    bucket = index.get(key)
    if bucket is not None:
        bucket.discard(placeholder)
        if not bucket:
            del index[key]


def _has_joined_key(keys: Iterable[str]) -> bool:
    """Whether any of ``keys`` contains a hyphen or apostrophe variant."""
    return bool(HAS_JOINER.search("\n".join(keys)))


def _blocklistable(keys: Iterable[str]) -> List[str]:
    """``keys`` without short tokens (<=2 chars) and stopwords."""
    return [key for key in keys if len(key) > 2 and key not in SANITIZER_STOPWORDS]


def _split_parts(token: str) -> List[str]:
    """Non-empty word runs of a joined token, joiners dropped."""
    return [part for part in _JOINER_SPLIT.split(token)[0::2] if part]


def _spaced(token: str) -> str:
    """A joined token written with spaces: "jean-pierre" -> "jean pierre"."""
    return " ".join(_split_parts(token))


def _plain_runs(ordered: Sequence[str]) -> Set[str]:
    """Spaced runs of two or more consecutive plain tokens, in written order."""
    runs: Set[str] = set()
    for start in range(len(ordered)):
        for end in range(start + 2, min(len(ordered), start + _MAX_JOINED_PARTS) + 1):
            if HAS_JOINER.search(ordered[end - 1]) or HAS_JOINER.search(ordered[start]):
                break
            runs.add(" ".join(ordered[start:end]))
    return runs


def _fuse_query(forms: "_Coref", known: Set[str]) -> Set[str]:
    """Query tokens with each spaced run of a known joined token read as that token.

    The run must be written in the same order ("Jean Pierre" for
    "jean-pierre", never "Pierre Jean").
    """
    fused = forms.whole
    line = None
    for joined in known:
        if joined in fused or not HAS_JOINER.search(joined):
            continue
        if line is None:
            line = " " + " ".join("|" if HAS_JOINER.search(t) else t for t in forms.ordered) + " "
        spaced = _spaced(joined)
        if f" {spaced} " in line:
            fused = (fused - set(spaced.split())) | {joined}
    return fused


def _fuse_known(
    known: AbstractSet[str], sequences: Callable[[], List[Tuple[str, ...]]], tokens: Set[str]
) -> AbstractSet[str]:
    """Known tokens with each joined query token written spaced, in order, read as that token.

    ``sequences`` yields the known originals' tokens in written order; it is
    only called when the query has a joined token.
    """
    fused = known
    runs: Optional[Set[str]] = None
    for joined in tokens:
        if joined in fused or not HAS_JOINER.search(joined):
            continue
        if runs is None:
            runs = set().union(*(_plain_runs(sequence) for sequence in sequences()))
        spaced = _spaced(joined)
        if spaced in runs:
            fused = (fused - set(spaced.split())) | {joined}
    return fused


def _apostrophe_suffix_at(token: str, any_case: bool = False) -> Optional[int]:
    """Index of the apostrophe that starts a suffix in ``token``, or None.

    A suffix is an apostrophe after at least three characters, before a
    lowercase letter, whose continuation (up to the next joiner) is in
    ``APOSTROPHE_SUFFIX_TAILS``: "kaya’nın", "O'Brien's". Any other
    continuation is part of the name ("Лукʼяненко", "Anne’Marie", "O'Brien",
    "Sa'id"), and so is a digit after the apostrophe. ``any_case`` accepts a
    capital too, for an all-caps token where case says nothing
    ("KAYA’NIN").
    """
    for index in range(3, len(token) - 1):
        if token[index] not in APOSTROPHES:
            continue
        following = token[index + 1]
        if not following.isalpha() or not (any_case or following.islower()):
            continue
        if _is_suffix_tail(token[index + 1 :].lower()):
            return index
    return None


def _apostrophe_join_at(token: str) -> Optional[int]:
    """Index of the first apostrophe after three characters that a letter follows, or None."""
    for index in range(3, len(token) - 1):
        if token[index] in APOSTROPHES and token[index + 1].isalpha():
            return index
    return None


def _token_forms(raw: str, language: str) -> Tuple[str, List[str], Optional[str]]:
    """(whole, parts, all-caps join) of one whitespace token of a name.

    ``whole`` is the token normalized, with a trailing suffix (see
    ``_apostrophe_suffix_at``) and a possessive cut off and joiners folded.
    ``parts`` are its joined parts (see ``_joined_parts``).

    An all-caps token with an apostrophe join ("ANNE’MARIE", "KAYA’NIN") has
    no case signal, so the tail decides. A tail in ``APOSTROPHE_SUFFIX_TAILS``
    is a suffix: ``whole`` is the head ("kaya", so "ALİ KAYA’NIN" corefers
    with "Ali Kaya") and the whole folded token is returned as the join, so
    text masks it whole. Any other tail makes the token one joined name
    ("anne'marie"), whose head is a part. The tail never becomes a key.
    """
    if raw.isupper():
        join_at = _apostrophe_join_at(raw)
        if join_at is not None:
            head = _whole_form(raw[:join_at], language)
            join = normalize_for_comparison(raw, language).translate(_JOINER_FOLD)
            if _is_suffix_tail(raw[join_at + 1 :].lower()):
                return head, _joined_parts(head, raw[:join_at]), join
            return join, [head, *_joined_parts(head, raw[:join_at])], None
    cut = _apostrophe_suffix_at(raw)
    if cut is not None:
        raw = raw[:cut]
    whole = _whole_form(raw, language)
    return whole, _joined_parts(whole, raw), None


def _whole_form(raw: str, language: str) -> str:
    """Comparison form of one name token: normalized, possessive stripped, joiners folded."""
    return strip_possessive_suffix(normalize_for_comparison(raw, language), language).translate(
        _JOINER_FOLD
    )


def _is_suffix_tail(tail: str) -> bool:
    """Whether the text after an apostrophe, to the end of the token, is only suffixes.

    Stacked suffixes count ("nın’a" of "Kaya’nın’a"); anything else after a
    later joiner makes the apostrophe part of the name, so the "'u" of
    "Nasr'u'llah" or "Sayf'u'd-Din" is no suffix.
    """
    pieces = _JOINER_SPLIT.split(tail)
    if any(joiner not in APOSTROPHES for joiner in pieces[1::2]):
        return False
    return all(word in APOSTROPHE_SUFFIX_TAILS for word in pieces[0::2])


def _joined_parts(token: str, raw: str) -> List[str]:
    """Parts of a folded joined token; ``raw`` is the token as written.

    A part after an apostrophe is left out when it is a suffix in any
    language ("Kaya’NIN" gives "kaya", never "nin"), and, when the token is
    written with a capital, when it starts with a lowercase letter: then it
    continues the name ("Лукʼяненко") rather than starting one, and on its
    own it is no name ("яненко"). A key without case ("o'brien") keeps
    every other part.
    """
    pieces = _JOINER_SPLIT.split(token)
    if len(pieces) == 1:
        return []
    written = _JOINER_SPLIT.split(normalize_unicode(raw))[2::2]
    cased = len(written) == len(pieces) // 2 and not raw.islower()
    parts = [pieces[0]]
    for index, (joiner, word) in enumerate(zip(pieces[1::2], pieces[2::2])):
        if joiner == "'" and (
            word in APOSTROPHE_SUFFIX_TAILS or (cased and written[index][:1].islower())
        ):
            continue
        parts.append(word)
    return [part for part in parts if part]


def _name_forms(value: str, language: str) -> List[Tuple[str, List[str], Optional[str]]]:
    """``_token_forms`` of every whitespace token of ``value``; a fast path without joiners."""
    if not HAS_JOINER.search(value):
        return [(token, [], None) for token in normalize_for_comparison(value, language).split()]
    return [_token_forms(raw, language) for raw in normalize_unicode(value).split()]


def _name_keys(value: str, language: str, normalized: Optional[str] = None) -> List[str]:
    """Blocklist keys of a name, in order and without repeats.

    Per token: the all-caps join if any, the whole token with hyphen and
    apostrophe variants folded, then its joined parts: "Jean-Pierre O’Brien"
    gives "jean-pierre", "jean", "pierre", "o'brien", "o", "brien". Callers
    drop short keys. ``normalized`` may carry the comparison form of a
    joiner-free ``value``.
    """
    if normalized is not None:
        return list(dict.fromkeys(normalized.split()))
    keys: List[str] = []
    for whole, parts, join in _name_forms(value, language):
        if join:
            keys.append(join)
        keys.append(whole)
        keys.extend(parts)
    return list(dict.fromkeys(key for key in keys if key))


class _Coref(NamedTuple):
    """Coreference forms of a name: whole tokens, joined parts, all tokens in order."""

    whole: Set[str]
    parts: Set[str]
    ordered: Tuple[str, ...]


def _plain_coref(normalized: str) -> _Coref:
    """Coreference forms of an already normalized name without any joiner."""
    ordered = tuple(normalized.split())
    return _Coref({token for token in ordered if len(token) > 2}, set(), ordered)


def _coreference_forms(value: str, language: str) -> _Coref:
    """Coreference forms of a name; short tokens and parts are left out of the sets."""
    if not HAS_JOINER.search(value):
        return _plain_coref(normalize_for_comparison(value, language))
    whole: Set[str] = set()
    parts: Set[str] = set()
    ordered: List[str] = []
    for token, token_parts, _join in _name_forms(value, language):
        ordered.append(token)
        if len(token) > 2:
            whole.add(token)
        parts.update(part for part in token_parts if len(part) > 2)
    return _Coref(whole, parts, tuple(ordered))


def _surface_form(
    original: str, token: str, language: str, normalized: Optional[str] = None
) -> Tuple[str, bool]:
    """The part of ``original`` written as ``token``, possessive stripped.

    The flag tells whether that part is a plain word whose comparison form
    is ``token`` itself. A name without any joiner is matched word by word,
    with one normalization (``normalized`` when the caller has it); otherwise
    joined runs are tried before single words, and ``token`` itself is the
    fallback when no part matches.
    """
    if normalized is not None or not HAS_JOINER.search(original):
        if normalized is None:
            normalized = normalize_for_comparison(original, language)
        word = _plain_word_written_as(original, normalized, token)
        if word is not None:
            return word, True
    runs = [m.group(0) for m in _TEXT_TOKEN.finditer(original)]
    runs.extend(m.group(0) for m in _WORD.finditer(original))
    for run in runs:
        stem = strip_possessive_suffix(run, language)
        if _name_keys(stem, language)[:1] == [token]:
            return stem, False
    return token, False


def _plain_word_written_as(original: str, normalized: str, token: str) -> Optional[str]:
    """The word of joiner-free ``original`` whose comparison form is ``token``, or None."""
    words = original.split()
    keys = normalized.split()
    if len(words) == len(keys):
        for word, key in zip(words, keys):
            if key == token:
                return word
    return None


def _has_apostrophe_suffix(value: str) -> bool:
    """Whether any whitespace token of ``value`` carries an apostrophe suffix.

    Same suffix rule as ``_token_forms``, applied to every token, so "Kaya’nın Ali"
    and "Ali Kaya’nın" both have one and "O’Brien" does not. An all-caps token
    has one only when its tail is a known suffix ("KAYA’NIN", not
    "ANNE’MARIE"); a mixed-case token is checked as written, so "Anne’Marie"
    keeps its capital tail whole.
    """
    for token in value.split():
        if _apostrophe_suffix_at(token, any_case=token.isupper()) is not None:
            return True
    return False


def pick_restoration_original(originals: Sequence[str]) -> str:
    """The original a placeholder restores to, out of the ones registered for it.

    A bare form (no apostrophe suffix) beats its suffixed variant ("Ali Kaya"
    over "Ali Kaya’nın"), so a placeholder echoed with a suffix restores once.
    Among bare forms the most complete one wins, but only when it is one name
    chain: its tokens must contain every other bare form's tokens, so "Ali
    Yılmaz" beats "Ali" and "John Smith" beats "Smith". Unrelated bare forms
    ("Jane Mary Smith" against "John Smith") fall back to the first registered
    bare form. Failing any bare form, the first registered original is used.
    """
    if len(originals) == 1:
        return originals[0]
    bare = [original for original in originals if not _has_apostrophe_suffix(original)]
    if not bare:
        return originals[0]
    tokenised = [(original, _chain_tokens(original)) for original in bare]
    most, most_tokens = max(tokenised, key=lambda pair: len(pair[1]))
    if all(tokens <= most_tokens for _, tokens in tokenised):
        return most
    return bare[0]


def _chain_tokens(original: str) -> frozenset[str]:
    """Case-folded words of ``original`` plus the parts of its joined words."""
    tokens: Set[str] = set()
    for word in original.casefold().translate(_JOINER_FOLD).split():
        tokens.add(word)
        tokens.update(part for part in _JOINER_SPLIT.split(word)[0::2] if part)
    return frozenset(tokens)


def restoration_lookup(pairs: Iterable[Tuple[str, str]]) -> Dict[str, str]:
    """Map each placeholder to its restoration original from ``(original, placeholder)`` pairs."""
    candidates: Dict[str, List[str]] = {}
    for original, placeholder in pairs:
        if placeholder:
            candidates.setdefault(placeholder, []).append(original)
    return {p: pick_restoration_original(o) for p, o in candidates.items()}


def _placeholder_entity_type(placeholder: str) -> str:
    """Return the entity type encoded in a ``[ENTITY_TYPE_N]`` placeholder."""
    return placeholder.strip("[]").rpartition("_")[0]


def _alias_partner(placeholder: str) -> Optional[str]:
    """The short form of a full placeholder or the full form of a short one, else None."""
    match = PLACEHOLDER_SHAPE.fullmatch(placeholder)
    if match is None:
        return None
    entity_type, index = match.groups()
    partner = PLACEHOLDER_SHORT_ALIASES.get(entity_type) or _ALIAS_FULL_FORMS.get(entity_type)
    return f"[{partner}_{index}]" if partner else None


def placeholder_order(placeholder: str) -> Tuple[str, int, str]:
    """Sort key of a placeholder: entity type, then numeric index, then the text."""
    entity_type, _, index = placeholder.strip("[]").rpartition("_")
    return entity_type, int(index) if index.isdigit() else -1, placeholder
