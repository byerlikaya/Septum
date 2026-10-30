"""Offline public-suffix lookups for every in-process ``tldextract`` caller.

Presidio's ``EmailRecognizer.validate_result`` calls the module-level
``tldextract.extract`` (the detector replaces that recognizer, but a host
may still build a Presidio analyzer with it), which delegates to the shared
``tldextract.tldextract.TLD_EXTRACTOR``. That default extractor fetches the
Public Suffix List from publicsuffix.org (then raw.githubusercontent.com)
whenever its disk cache under ``~/.cache`` is empty, and writes the result
back there: network egress and a home-directory write from the air-gapped
zone.

:func:`use_bundled_suffix_list` swaps that shared extractor for one with no
fetch URLs and no disk cache, so lookups always come from the suffix-list
snapshot shipped inside the ``tldextract`` wheel. No network client is
imported here; ``tldextract`` is already a Presidio dependency.
"""

from __future__ import annotations

import tldextract
import tldextract.tldextract as _tldextract_module

_OFFLINE_EXTRACTOR = tldextract.TLDExtract(
    suffix_list_urls=(),
    cache_dir=None,
    fallback_to_snapshot=True,
)


def use_bundled_suffix_list() -> None:
    """Point every ``tldextract`` entry point at the bundled snapshot.

    Replaces the shared ``TLD_EXTRACTOR`` and also binds the public
    ``tldextract.extract`` names (package attribute and module function) to
    the offline extractor, so a caller that resolves either one never reaches
    the fetching default.

    The shared extractor is a private ``tldextract`` global. If a release
    renames it or changes its type, assigning to it would no longer redirect
    anything and lookups would silently fall back to fetching the suffix list
    over the network; refusing at import is safer than that egress, so this
    raises :class:`RuntimeError` instead.

    Idempotent and process-wide. Called when :mod:`septum_core.detector` is
    imported, before any Presidio analyzer exists, so no recognizer ever
    sees the fetching default extractor.
    """
    current = getattr(_tldextract_module, "TLD_EXTRACTOR", None)
    if not isinstance(current, tldextract.TLDExtract):
        raise RuntimeError(
            "tldextract.tldextract.TLD_EXTRACTOR is missing or is not a "
            "TLDExtract instance; the installed tldextract release is not "
            "supported, and public-suffix lookups cannot be kept offline."
        )
    _tldextract_module.TLD_EXTRACTOR = _OFFLINE_EXTRACTOR
    _tldextract_module.extract = _OFFLINE_EXTRACTOR
    tldextract.extract = _OFFLINE_EXTRACTOR
