from __future__ import annotations

"""
Local de-anonymization strategies for septum-core.

Turns LLM responses that still contain placeholders (for example
``[PERSON_NAME_1]``) back into human-readable text using only the
in-memory :class:`AnonymizationMap`. Never sends the anonymization
map or any other sensitive metadata to remote services — the whole
class runs inside the air-gapped core package.

Hosts that want a smarter replacement policy (for example the
Ollama-backed deanonymizer strategy used by the backend) wrap this
class and fall back to :meth:`Unmasker.unmask` when their strategy
is unavailable.
"""

import re
from typing import Collection, Optional

from .anonymization_map import AnonymizationMap


class Unmasker:
    """Replace placeholders with their original values using an anonymization map.

    The current implementation performs direct string replacement and also
    understands a small set of short placeholder aliases that LLMs emit in
    place of the full form (``[PERSON_1]`` instead of ``[PERSON_NAME_1]``).
    A short form that is itself a mapped placeholder is never treated as an
    alias, so it resolves to its own original.
    """

    _PLACEHOLDER_SHORT_ALIASES = (
        (re.compile(r"^\[PERSON_NAME_(\d+)\]$"), "PERSON"),
        (re.compile(r"^\[ORGANIZATION_NAME_(\d+)\]$"), "ORGANIZATION"),
    )

    def unmask(
        self,
        text: str,
        anon_map: AnonymizationMap,
        allowed_placeholders: Optional[Collection[str]] = None,
    ) -> str:
        """Return a de-anonymized copy of ``text`` using ``anon_map``.

        When ``allowed_placeholders`` is given, only those placeholders (and
        their short aliases) are restored; every other placeholder in
        ``text`` stays literally as is. Hosts pass the placeholders that
        actually appeared in the prompt sent to the LLM, so an answer cannot
        pull originals out of the map that the turn never exposed. ``None``
        keeps the historical behaviour of restoring every mapped placeholder.

        Iteration is keyed by **placeholder**, not by original. The chat-time
        multi-document unification can legitimately assign two distinct
        placeholders to a single original string (e.g. when the name was
        detected as PERSON_NAME in one document and ORGANIZATION_NAME in
        another) and the original-keyed ``entity_map`` representation
        silently drops one of those entries through dict overwrite. The
        reversed ``placeholder_lookup`` field carries every placeholder
        the unification minted, so every placeholder the cloud LLM echoes
        back has a chance to resolve. Per-document maps that never set
        ``placeholder_lookup`` fall back to deriving it from
        ``entity_map``, preserving the single-document path verbatim.
        """
        if not text:
            return text

        if anon_map.placeholder_lookup:
            lookup = anon_map.placeholder_lookup
        elif anon_map.entity_map:
            lookup = {
                placeholder: original
                for original, placeholder in anon_map.entity_map.items()
                if placeholder
            }
        else:
            return text

        result = text
        for placeholder, original in lookup.items():
            if not placeholder:
                continue
            if allowed_placeholders is not None and placeholder not in allowed_placeholders:
                continue
            if placeholder in result:
                result = result.replace(placeholder, original)
            for pattern, short_prefix in self._PLACEHOLDER_SHORT_ALIASES:
                match = pattern.match(placeholder)
                if match:
                    short_form = f"[{short_prefix}_{match.group(1)}]"
                    if short_form in lookup:
                        break
                    if short_form in result:
                        result = result.replace(short_form, original)
                    break
        return result
