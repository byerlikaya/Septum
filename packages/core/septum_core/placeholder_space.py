"""One placeholder space out of several anonymization maps.

Every document map numbers its placeholders on its own, so two documents
can both hold ``[PERSON_NAME_1]`` for different people, and one person can
carry a different placeholder in each. A prompt that mixes chunks of
several documents needs one numbering: :func:`unify_placeholder_spaces`
gives every per-map placeholder exactly one global placeholder and builds
the unified map those globals restore through.

A per-map placeholder is the unit that moves, never a single original, so
a document's own coreference decisions survive the renumbering, and two
placeholders one map keeps apart never share a global. Two placeholders of
different maps share a global only when nothing else can claim it:

* a non-person value joins the one global holding an equal value of the
  same entity type;
* a person with a full name (a group of two or more name tokens) joins the
  global of the same entity type whose full names are exactly its own. A
  name that is only part of another ("John Smith" against "John Michael
  Smith") never merges, nor does a person one map knows by more full names
  than another, and two existing globals never merge;
* a person known only by one bare token ("Ali") joins the global that holds
  nothing but that bare token. The query map, placed after every document,
  may also join the one person a bare token names (the rule a single map
  applies to "Ali" after "Ali Yılmaz") when no bare-only global exists; a
  document never does, because two documents' "Ali" can be two people.

Tokens two global people share get a neutral placeholder that restores to
the token alone, through the map's own shared-token rule. The result does
not depend on the order maps, placeholders or originals were inserted in.
The unified map is never persisted; it holds raw PII and lives for one
request.
"""

from __future__ import annotations

from typing import (
    AbstractSet,
    Dict,
    FrozenSet,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    cast,
)

from .anonymization_map import (
    BLOCKLIST_ENTITY_TYPES,
    HAS_JOINER,
    PLACEHOLDER_SHAPE,
    AnonymizationMap,
    pick_restoration_original,
    placeholder_order,
)
from .text_utils import normalize_for_comparison

# Key of the query map among the maps of one turn.
QUERY_MAP_KEY = -1

PlaceholderRemaps = Dict[int, Dict[str, str]]


def unify_placeholder_spaces(
    maps: Sequence[Tuple[int, AnonymizationMap]],
) -> Tuple[PlaceholderRemaps, AnonymizationMap]:
    """Return ``(remaps, unified)`` for ``(key, map)`` pairs.

    ``remaps[key]`` maps every placeholder of that map (in ``entity_map``
    or ``token_to_placeholder``) to its global placeholder, identity
    entries included, and no two placeholders of one map to the same
    global. ``unified`` holds every original under its global, the token
    table of the global space and a ``placeholder_lookup`` for every
    restorable global. A placeholder only seen in a token table gets a
    global of its own that restores to nothing, so it stays masked. A
    placeholder whose shape carries no index is kept as it is by the first
    map holding it and renamed for every other. Maps are taken in key
    order; the input maps are not changed.
    """
    return _Unifier(sorted(maps, key=lambda item: item[0])).run()


def settle_strays(
    text: str, remap: Dict[str, str], known: Set[str], unified: AnonymizationMap
) -> None:
    """Extend ``remap`` over the placeholders of ``text`` its source map does not hold.

    ``known`` holds the source map's placeholders. A stray placeholder (text
    edited by hand, a placeholder the user typed) stands for nothing, so it
    must not land on a global of the unified space: it is kept as written
    when the space does not use it, else renamed to an unused one (see
    :meth:`AnonymizationMap.inert`). Either way it restores to nothing.
    Kept strays join ``known`` and renamed ones ``remap``, so a stray keeps
    its placeholder across every text of the same source.
    """
    for match in PLACEHOLDER_SHAPE.finditer(text or ""):
        placeholder = match.group(0)
        if placeholder in remap or placeholder in known:
            continue
        settled = unified.inert(placeholder)
        if settled == placeholder:
            known.add(placeholder)
        else:
            remap[placeholder] = settled


def map_placeholders(anon_map: AnonymizationMap) -> Set[str]:
    """Every placeholder ``anon_map`` holds an entry or a token for."""
    return {*anon_map.entity_map.values(), *anon_map.token_to_placeholder.values()}


def _original_order(original: str) -> Tuple[bool, str]:
    """Sort key of an original: written forms before all-caps ones, then the text.

    The order decides which of two equally complete forms a global restores
    to ("Ahmet Kaya" over "AHMET KAYA"), whatever order a map was filled in.
    """
    return original.isupper(), original


def _originals_by_placeholder(anon_map: AnonymizationMap) -> Dict[str, List[str]]:
    """Originals of each placeholder, both in a fixed order (placeholder index, then text)."""
    found: Dict[str, List[str]] = {}
    for original, placeholder in anon_map.entity_map.items():
        originals = found.get(placeholder)
        if originals is None:
            found[placeholder] = [original]
        else:
            originals.append(original)
    ordered: Dict[str, List[str]] = {}
    for placeholder in sorted(found, key=placeholder_order):
        originals = found[placeholder]
        ordered[placeholder] = originals if len(originals) == 1 else sorted(originals, key=_original_order)
    return ordered


class _Deferred:
    """A bare-only person placeholder waiting for the second pass."""

    __slots__ = ("key", "placeholder", "token", "originals")

    def __init__(self, key: int, placeholder: str, token: str, originals: List[str]) -> None:
        self.key = key
        self.placeholder = placeholder
        self.token = token
        self.originals = originals


class _Unifier:
    """State of one unification run (see :func:`unify_placeholder_spaces`)."""

    def __init__(self, maps: List[Tuple[int, AnonymizationMap]]) -> None:
        self.maps = maps
        language = maps[0][1].language if maps else "en"
        self.unified = AnonymizationMap(document_id=0, language=language)
        self.remaps: PlaceholderRemaps = {key: {} for key, _ in maps}
        # The global holding each exact set of full names (or of several bare
        # names), per entity type.
        self.full_owner: Dict[Tuple[str, FrozenSet[FrozenSet[str]]], str] = {}
        # The bare-only global of each bare token, per entity type.
        self.bare_owner: Dict[Tuple[str, str], str] = {}
        # Keys of the maps whose placeholders each global already holds.
        self.members: Dict[str, Set[int]] = {}
        self.adopted: Set[Tuple[str, str]] = set()
        self.types: Dict[str, Optional[str]] = {}
        # Comparison forms of joiner-free originals, for this run only.
        self.forms: Dict[str, str] = {}
        # Originals each global restores from, in assignment order.
        self.restores: Dict[str, List[str]] = {}
        self.deferred: List[_Deferred] = []

    def run(self) -> Tuple[PlaceholderRemaps, AnonymizationMap]:
        for key, anon_map in self.maps:
            for placeholder, originals in _originals_by_placeholder(anon_map).items():
                self._first_pass(key, anon_map, placeholder, originals)
        # Documents first: the query's bare names then find their bare-only globals.
        self.deferred.sort(key=lambda item: item.key == QUERY_MAP_KEY)
        for item in self.deferred:
            self._join_bare(item)
        for key, anon_map in self.maps:
            self._orphans(key, anon_map)
        self._extra_tokens()
        self._attach_lookup()
        return self.remaps, self.unified

    def _form(self, original: str) -> Optional[str]:
        """The comparison form of a joiner-free ``original`` (memoised), else None."""
        form = self.forms.get(original)
        if form is None and not HAS_JOINER.search(original):
            form = self.forms[original] = normalize_for_comparison(original, self.unified.language)
        return form

    def _type(self, placeholder: str) -> Optional[str]:
        """The entity type of ``placeholder``, or None when its shape has no index."""
        entity_type = self.types.get(placeholder, "")
        if entity_type == "":
            match = PLACEHOLDER_SHAPE.fullmatch(placeholder)
            entity_type = self.types[placeholder] = match.group(1) if match else None
        return entity_type

    def _first_pass(
        self, key: int, anon_map: AnonymizationMap, placeholder: str, originals: List[str]
    ) -> None:
        """Place one per-map placeholder, or defer it when it is a bare-only person."""
        entity_type = self._type(placeholder)
        if entity_type is None:
            self._assign(key, placeholder, originals, self._shapeless(placeholder), "")
            return
        if entity_type in BLOCKLIST_ENTITY_TYPES and self._place_person(
            key, anon_map, placeholder, originals, entity_type
        ):
            return
        chosen = self._free(key, self._value_owner(entity_type, originals))
        self._assign(key, placeholder, originals, chosen or self.unified.mint(entity_type))

    def _place_person(
        self,
        key: int,
        anon_map: AnonymizationMap,
        placeholder: str,
        originals: List[str],
        entity_type: str,
    ) -> bool:
        """Place or defer a person placeholder by its name groups; False when it has no name token."""
        groups = anon_map.person_group_keys(placeholder)
        full = frozenset([group for group in groups if len(group) >= 2])
        if full:
            self._assign(key, placeholder, originals, self._join_full(key, entity_type, full))
            return True
        bare = {group for group in groups if group}
        if not bare:
            return False
        if len(bare) == 1 and len(groups) == 1:
            (token,) = next(iter(bare))
            self.deferred.append(_Deferred(key, placeholder, token, originals))
        else:
            # Several bare tokens under one placeholder ("Demir-Kaya" and "Demir"):
            # it merges only with a placeholder holding exactly the same ones.
            self._assign(key, placeholder, originals, self._join_full(key, entity_type, frozenset(bare)))
        return True

    def _shapeless(self, placeholder: str) -> str:
        """The global of a placeholder without an index: itself for the first map holding it.

        Another map's placeholder of the same text may stand for another
        value, so it gets a global of its own, typed by the placeholder's text.
        """
        if placeholder not in self.members:
            return placeholder
        return self.unified.mint(placeholder.strip("[]") or "PLACEHOLDER")

    def _free(self, key: int, chosen: Optional[str]) -> Optional[str]:
        """``chosen`` unless it already holds another placeholder of map ``key``."""
        if chosen is not None and key in self.members.get(chosen, ()):
            return None
        return chosen

    def _value_owner(self, entity_type: str, originals: List[str]) -> Optional[str]:
        """The one global holding an equal value of ``entity_type``, or None."""
        found = {
            self.unified.known_value(original, entity_type, self._form(original))
            for original in originals
        }
        found.discard(None)
        return cast(str, next(iter(found))) if len(found) == 1 else None

    def _join_full(self, key: int, entity_type: str, full: FrozenSet[FrozenSet[str]]) -> str:
        """The global of ``entity_type`` whose name groups are exactly ``full``, else a new one."""
        owner_key = (entity_type, full)
        chosen = self._free(key, self.full_owner.get(owner_key))
        if chosen is None:
            chosen = self.unified.mint(entity_type)
            self.full_owner.setdefault(owner_key, chosen)
        return chosen

    def _join_bare(self, item: _Deferred) -> None:
        """Second pass: place a bare-only person placeholder."""
        entity_type = self._type(item.placeholder)
        if entity_type is None:
            raise RuntimeError("a deferred placeholder has no entity type")
        owner_key = (entity_type, item.token)
        chosen = self.bare_owner.get(owner_key) or self.unified.bare_only_holder(
            item.token, entity_type
        )
        chosen = self._free(item.key, chosen)
        if chosen is None and item.key == QUERY_MAP_KEY:
            person = self._free(item.key, self.unified.coreferent_person(item.originals[0]))
            if person is not None and self._type(person) == entity_type:
                self._assign(item.key, item.placeholder, item.originals, person)
                return
        if chosen is None:
            chosen = self.unified.mint(entity_type)
        self.bare_owner.setdefault(owner_key, chosen)
        self._assign(item.key, item.placeholder, item.originals, chosen)

    def _assign(
        self,
        key: int,
        placeholder: str,
        originals: List[str],
        chosen: str,
        entity_type: Optional[str] = None,
    ) -> None:
        """Map ``placeholder`` of map ``key`` to ``chosen`` and register its originals there."""
        self.remaps[key][placeholder] = chosen
        members = self.members.get(chosen)
        if members is None:
            self.members[chosen] = {key}
        else:
            members.add(key)
        if entity_type is None:
            entity_type = self._type(chosen) or ""
        adopted, form = self.adopted, self._form
        fresh: List[str] = []
        for original in originals:
            pair = (original, chosen)
            if pair not in adopted:
                adopted.add(pair)
                fresh.append(original)
                form(original)
        if fresh:
            self.unified.adopt_many(fresh, chosen, entity_type, self.forms)
        restores = self.restores.get(chosen)
        if restores is None:
            self.restores[chosen] = list(originals)
        else:
            restores.extend(originals)

    def _orphans(self, key: int, anon_map: AnonymizationMap) -> None:
        """Give each placeholder only seen in the token table a global that restores to nothing."""
        remap = self.remaps[key]
        orphans = {p for p in anon_map.token_to_placeholder.values() if p not in remap}
        for placeholder in sorted(orphans, key=placeholder_order):
            entity_type = self._type(placeholder)
            remap[placeholder] = placeholder if entity_type is None else self.unified.mint(entity_type)

    def _token_claims(self, table: AbstractSet[str]) -> Dict[str, Dict[int, str]]:
        """Per token key no original yields: the global each map points it at."""
        claims: Dict[str, Dict[int, str]] = {}
        for key, anon_map in self.maps:
            self.unified.blocklist.update(anon_map.blocklist)
            remap = self.remaps[key]
            for token, placeholder in anon_map.token_to_placeholder.items():
                if token not in table:
                    claims.setdefault(token, {})[key] = remap[placeholder]
        return claims

    def _extra_tokens(self) -> None:
        """Carry over token keys no original yields (pronouns, keys of older maps).

        A key keeps its global only when every map of the turn points it at
        that same global. Otherwise it names nobody in particular: it gets a
        neutral placeholder that restores to the key itself. Keys are taken
        in sorted order, so the numbering does not depend on the order a
        token table was filled in.
        """
        table = self.unified.token_to_placeholder
        claims = self._token_claims(table.keys())
        for token in sorted(claims):
            globals_ = set(claims[token].values())
            if len(claims[token]) == len(self.maps) and len(globals_) == 1:
                table[token] = next(iter(globals_))
                continue
            first = min(globals_, key=placeholder_order)
            neutral = self.unified.mint(self._type(first) or "PERSON_NAME")
            table[token] = neutral
            self.restores[neutral] = [token]
        self.unified.blocklist.update(table)

    def _attach_lookup(self) -> None:
        """Set ``placeholder_lookup``: every global with originals, neutrals included."""
        lookup = {p: pick_restoration_original(o) for p, o in self.restores.items()}
        minted: Dict[str, List[str]] = {}
        for original, placeholder in self.unified.entity_map.items():
            if placeholder not in lookup:
                minted.setdefault(placeholder, []).append(original)
        for placeholder, originals in minted.items():
            lookup[placeholder] = pick_restoration_original(originals)
        self.unified.placeholder_lookup = lookup


__all__ = [
    "QUERY_MAP_KEY",
    "PlaceholderRemaps",
    "map_placeholders",
    "settle_strays",
    "unify_placeholder_spaces",
]
