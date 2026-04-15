from __future__ import annotations

"""
Recognizer registry and regulation-specific packs for Septum.

This package is now a thin backward-compatibility shim over
:mod:`septum_core.recognizers`. Regulation packs live under
``septum_core.recognizers.<regulation_id>``; the backend-side
``RecognizerRegistry`` re-exported from :mod:`.registry` adds the
LLM-backed custom recognizer factory that depends on local Ollama.
"""
