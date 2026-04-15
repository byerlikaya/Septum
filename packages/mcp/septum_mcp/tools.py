from __future__ import annotations

"""
Pure-Python implementations of the six septum-mcp tools.

Each function in this module takes a :class:`SeptumEngine` (or, for
``list_regulations``, nothing) and returns a JSON-serializable ``dict``
with an ``ok`` flag. The MCP server layer (``septum_mcp.server``)
wraps these functions so the SDK-specific response handling stays
isolated from the business logic. Tests import and call these
functions directly without spinning up a stdio transport.

Return envelope::

    {
        "ok": True,
        "data": {...},        # present on success
    }
    {
        "ok": False,
        "error": "message",   # present on failure
    }

The envelope keeps error handling uniform across all tools and lets
the MCP layer convert a failing call into an ``isError`` tool result
without re-parsing exception traces.
"""

import importlib
import logging
from typing import Any, Dict, Iterable, List, Optional

from septum_core import SeptumEngine

from .file_readers import SUPPORTED_EXTENSIONS, read_file

logger = logging.getLogger(__name__)

BUILTIN_REGULATION_IDS: tuple[str, ...] = (
    "gdpr",
    "kvkk",
    "ccpa",
    "cpra",
    "hipaa",
    "pipeda",
    "lgpd",
    "pdpa_th",
    "pdpa_sg",
    "appi",
    "pipl",
    "popia",
    "dpdp",
    "uk_gdpr",
    "pdpl_sa",
    "nzpa",
    "australia_pa",
)


def _ok(data: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": True, "data": data}


def _err(message: str) -> Dict[str, Any]:
    return {"ok": False, "error": message}


def _require_string(value: Any, name: str) -> Optional[str]:
    if not isinstance(value, str):
        return None
    if not value.strip():
        return None
    return value


def _normalise_regulations(value: Any) -> Optional[List[str]]:
    if value is None:
        return None
    if isinstance(value, str):
        return [item.strip().lower() for item in value.split(",") if item.strip()]
    if isinstance(value, Iterable):
        result: List[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                result.append(item.strip().lower())
        return result
    return None


def mask_text(
    engine: SeptumEngine,
    *,
    text: Any,
    regulations: Any = None,
    language: Any = None,
) -> Dict[str, Any]:
    """Detect and mask PII in ``text``.

    The ``regulations`` argument is accepted for API compatibility with
    the tool schema but is currently ignored: the engine is pre-wired
    with the regulation set from :class:`MCPConfig`. Callers that want
    a different set should construct a fresh engine. The field is kept
    so future versions can rebuild a cached sub-engine per regulation
    subset without breaking existing clients.
    """
    _ = regulations  # reserved for future scoped-regulation support
    raw_text = _require_string(text, "text")
    if raw_text is None:
        return _err("'text' must be a non-empty string.")

    lang = _require_string(language, "language") or "en"
    try:
        result = engine.mask(raw_text, language=lang)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mask_text failed")
        return _err(f"mask_text failed: {exc}")

    return _ok(
        {
            "masked_text": result.masked_text,
            "session_id": result.session_id,
            "entity_count": result.entity_count,
            "entity_type_counts": result.entity_type_counts,
        }
    )


def unmask_response(
    engine: SeptumEngine,
    *,
    text: Any,
    session_id: Any,
) -> Dict[str, Any]:
    """Restore original values in an LLM response using ``session_id``."""
    raw_text = _require_string(text, "text")
    if raw_text is None:
        return _err("'text' must be a non-empty string.")
    sid = _require_string(session_id, "session_id")
    if sid is None:
        return _err("'session_id' must be a non-empty string.")

    if engine.get_session_map(sid) is None:
        return _err(
            f"Session '{sid}' was not found or has expired. "
            "Call mask_text again before unmasking."
        )

    try:
        restored = engine.unmask(raw_text, sid)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("unmask_response failed")
        return _err(f"unmask_response failed: {exc}")

    return _ok({"text": restored, "session_id": sid})


def detect_pii(
    engine: SeptumEngine,
    *,
    text: Any,
    regulations: Any = None,
    language: Any = None,
) -> Dict[str, Any]:
    """Scan ``text`` for PII without persisting a session.

    Internally reuses :meth:`SeptumEngine.mask` and immediately
    releases the session, so callers never receive a session id they
    cannot unmask. The detected spans are returned verbatim so tools
    like editor highlights can point at exact character offsets.
    """
    _ = regulations
    raw_text = _require_string(text, "text")
    if raw_text is None:
        return _err("'text' must be a non-empty string.")

    lang = _require_string(language, "language") or "en"
    try:
        result = engine.mask(raw_text, language=lang)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("detect_pii failed")
        return _err(f"detect_pii failed: {exc}")

    engine.release(result.session_id)

    entities = [
        {
            "start": span.start,
            "end": span.end,
            "entity_type": span.entity_type,
            "placeholder": span.placeholder,
            "score": span.score,
        }
        for span in result.detected_spans
    ]
    return _ok(
        {
            "entities": entities,
            "entity_count": result.entity_count,
            "entity_type_counts": result.entity_type_counts,
        }
    )


def scan_file(
    engine: SeptumEngine,
    *,
    file_path: Any,
    mask: Any = False,
    language: Any = None,
) -> Dict[str, Any]:
    """Read a file into text and run either detection or full masking.

    When ``mask`` is false (the default) the file is analysed in
    detect-only mode: the session is released immediately so no PII
    values are retained. When ``mask`` is true the full masked text
    and a ``session_id`` are returned so the caller can unmask a
    downstream LLM response later.
    """
    path = _require_string(file_path, "file_path")
    if path is None:
        return _err("'file_path' must be a non-empty string.")

    read_result = read_file(path)
    if not read_result.ok:
        return _err(read_result.error)
    if not read_result.text.strip():
        return _err(f"File '{path}' is empty or contains no extractable text.")

    do_mask = bool(mask)
    lang = _require_string(language, "language") or "en"
    try:
        result = engine.mask(read_result.text, language=lang)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("scan_file mask failed")
        return _err(f"scan_file failed: {exc}")

    entities = [
        {
            "start": span.start,
            "end": span.end,
            "entity_type": span.entity_type,
            "placeholder": span.placeholder,
            "score": span.score,
        }
        for span in result.detected_spans
    ]

    data: Dict[str, Any] = {
        "file_path": path,
        "format": read_result.format,
        "entity_count": result.entity_count,
        "entity_type_counts": result.entity_type_counts,
        "entities": entities,
    }
    if do_mask:
        data["masked_text"] = result.masked_text
        data["session_id"] = result.session_id
    else:
        engine.release(result.session_id)

    return _ok(data)


def list_regulations(active_regulations: Iterable[str] | None = None) -> Dict[str, Any]:
    """Return the catalog of built-in regulation packs.

    For each built-in pack id we walk the recognizer module (without
    instantiating the recognizers — that would pull in spaCy models)
    and return the set of entity types declared on each recognizer.
    When ``active_regulations`` is provided, those ids are flagged in
    the output so MCP clients can render an "active" badge without
    calling back.
    """
    active_set = {r.lower() for r in (active_regulations or [])}
    packs: List[Dict[str, Any]] = []

    for reg_id in BUILTIN_REGULATION_IDS:
        entity_types = _entity_types_for_pack(reg_id)
        packs.append(
            {
                "id": reg_id,
                "entity_types": entity_types,
                "is_active": reg_id in active_set,
            }
        )

    return _ok(
        {
            "regulations": packs,
            "active_regulation_ids": sorted(active_set),
        }
    )


def get_session_map(engine: SeptumEngine, *, session_id: Any) -> Dict[str, Any]:
    """Return the ``{original: placeholder}`` map for ``session_id``.

    Intended strictly for local debugging; the returned values contain
    raw PII and must never be forwarded to a remote system. MCP
    clients display this in a developer-tools panel.
    """
    sid = _require_string(session_id, "session_id")
    if sid is None:
        return _err("'session_id' must be a non-empty string.")

    mapping = engine.get_session_map(sid)
    if mapping is None:
        return _err(
            f"Session '{sid}' was not found or has expired. "
            "Call mask_text to create a new one."
        )

    return _ok(
        {
            "session_id": sid,
            "entries": [
                {"original": original, "placeholder": placeholder}
                for original, placeholder in mapping.items()
            ],
            "entry_count": len(mapping),
        }
    )


def _entity_types_for_pack(reg_id: str) -> List[str]:
    """Return declared entity types for a built-in regulation pack.

    Uses :mod:`importlib` to import the pack's ``recognizers`` module
    and walks each recognizer's ``supported_entities`` attribute. If
    the pack is missing or fails to load (for example because a
    transitive dependency is absent), an empty list is returned
    rather than raising — ``list_regulations`` degrades gracefully.
    """
    module_path = f"septum_core.recognizers.{reg_id}.recognizers"
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError:
        return []
    get_recognizers = getattr(module, "get_recognizers", None)
    if get_recognizers is None:
        return []
    try:
        recognizers = list(get_recognizers())
    except Exception:
        return []

    seen: set[str] = set()
    ordered: List[str] = []
    for recognizer in recognizers:
        for entity_type in getattr(recognizer, "supported_entities", ()):  # type: ignore[arg-type]
            if entity_type not in seen:
                seen.add(entity_type)
                ordered.append(entity_type)
    return ordered


SUPPORTED_FILE_EXTENSIONS: tuple[str, ...] = SUPPORTED_EXTENSIONS
