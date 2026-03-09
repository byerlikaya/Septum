from __future__ import annotations

"""
Recognizer registry and regulation-specific packs for Septum.

This package provides:
- `registry.RecognizerRegistry`: builds the Presidio recognizer set for the
  active regulations and custom rules.
- Regulation packs under `<regulation_id>.recognizers` (for example
  `gdpr.recognizers`) which expose a `get_recognizers()` function returning
  a list of `EntityRecognizer` instances.
"""


