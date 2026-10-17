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

import logging
import os
import re
import stat
import threading
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Tuple

from septum_core import SeptumEngine, Unmasker
from septum_core.recognizers import BUILTIN_REGULATION_IDS, entity_types_for

from .config import DEFAULT_MAX_UNMASK_PLACEHOLDERS
from .file_readers import (
    READ_CHANGED,
    READ_NOT_FOUND,
    READ_NOT_REGULAR,
    READ_TOO_LARGE,
    READ_UNSUPPORTED,
    SUPPORTED_EXTENSIONS,
    FileReadResult,
    read_file_beneath,
)

logger = logging.getLogger(__name__)


_DEFAULT_MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB
_BRACKET_TOKEN = re.compile(r"\[[^\[\]]+\]")


class _EmittedPlaceholderLedger:
    """Per-session placeholders handed to the MCP client, and those restored.

    ``unmask_response`` restores only emitted placeholders, so a caller
    cannot use the tool as an oracle for map entries it was never shown.
    It also tracks which placeholders each session has already restored,
    so the unmask cap is a cumulative per-session budget rather than a
    per-call one. Entries for sessions the engine has released or expired
    are pruned whenever the ledger outgrows the engine's live-session count.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_session: Dict[str, FrozenSet[str]] = {}
        self._restored: Dict[str, FrozenSet[str]] = {}

    def record(self, engine: SeptumEngine, session_id: str, masked_text: str) -> None:
        mapping = engine.get_session_map(session_id) or {}
        emitted = frozenset(
            placeholder
            for placeholder in mapping.values()
            if placeholder and placeholder in masked_text
        )
        live_count = engine.active_session_count()
        with self._lock:
            self._by_session[session_id] = emitted
            self._restored.pop(session_id, None)
            if len(self._by_session) > live_count:
                for sid in list(self._by_session):
                    if sid != session_id and engine.get_session_map(sid) is None:
                        del self._by_session[sid]
                        self._restored.pop(sid, None)

    def emitted(self, session_id: str) -> FrozenSet[str]:
        with self._lock:
            return self._by_session.get(session_id, frozenset())

    def restored(self, session_id: str) -> FrozenSet[str]:
        with self._lock:
            return self._restored.get(session_id, frozenset())

    def consume(self, session_id: str, placeholders: FrozenSet[str], cap: int) -> Optional[int]:
        """Charge ``placeholders`` to the session's restore budget.

        Already-restored placeholders are free. Returns ``None`` when the
        session's distinct restored set stays within ``cap`` (and records
        it), otherwise the size the set would have reached, recording
        nothing. Check and commit share one lock so concurrent calls
        cannot both pass against the same remaining budget.
        """
        with self._lock:
            restored = self._restored.get(session_id, frozenset())
            combined = restored | placeholders
            if len(combined) > cap:
                return len(combined)
            self._restored[session_id] = combined
            return None


_LEDGERS: "weakref.WeakKeyDictionary[SeptumEngine, _EmittedPlaceholderLedger]" = (
    weakref.WeakKeyDictionary()
)
_LEDGERS_LOCK = threading.Lock()


def _ledger_for(engine: SeptumEngine) -> _EmittedPlaceholderLedger:
    with _LEDGERS_LOCK:
        ledger = _LEDGERS.get(engine)
        if ledger is None:
            ledger = _EmittedPlaceholderLedger()
            _LEDGERS[engine] = ledger
        return ledger


class FileRootError(ValueError):
    """``SEPTUM_MCP_FILE_ROOT`` names no usable directory."""


@dataclass(frozen=True)
class FileRoot:
    """The ``scan_file`` root, pinned when it was loaded.

    ``lexical`` is the root as configured (``..``-collapsed, symlinks
    kept) so callers may spell paths through a symlinked root;
    ``resolved`` is its real target and the authoritative boundary;
    ``identity`` is that directory's ``(st_dev, st_ino)``, checked against
    the descriptor every read opens, so replacing the root path later
    (for instance with a symlink) refuses reads instead of moving the
    boundary.
    """

    lexical: Path
    resolved: Path
    identity: Tuple[int, int]


def load_file_root(raw: Optional[str]) -> Optional[FileRoot]:
    """Resolve and pin ``raw`` as the ``scan_file`` root; ``None`` when unset.

    Raises :class:`FileRootError` when ``raw`` is set but does not name an
    existing directory. A root configured behind a symlink is accepted and
    pinned to the directory it resolves to now.
    """
    if not raw:
        return None
    try:
        resolved = Path(raw).expanduser().resolve(strict=True)
        info = resolved.stat()
    except (OSError, RuntimeError, ValueError) as exc:
        raise FileRootError(
            f"SEPTUM_MCP_FILE_ROOT={raw!r} does not exist or is not accessible; "
            "the scan_file root must exist when the server starts"
        ) from exc
    if not stat.S_ISDIR(info.st_mode):
        raise FileRootError(f"SEPTUM_MCP_FILE_ROOT={raw!r} is not a directory")
    return FileRoot(_lexical_path(raw), resolved, (info.st_dev, info.st_ino))


def _lexical_path(path_str: str) -> Path:
    """Absolute, ``..``-collapsed form of ``path_str`` without touching the filesystem."""
    return Path(os.path.abspath(os.path.expanduser(path_str)))


def _relative_to_any(path: Path, roots: Iterable[Path]) -> Optional[Path]:
    for root in roots:
        try:
            return path.relative_to(root)
        except ValueError:
            continue
    return None


def _has_dot_part(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)


def _scan_file_max_size() -> int:
    raw = os.getenv("SEPTUM_MCP_MAX_FILE_BYTES", "").strip()
    if not raw:
        return _DEFAULT_MAX_FILE_SIZE_BYTES
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_MAX_FILE_SIZE_BYTES
    return max(1, value)


_SCAN_PATH_UNAVAILABLE = "file_path not found or not permitted"
_SCAN_READ_FAILED = "file_path could not be read"


@dataclass(frozen=True)
class _ScanTarget:
    """A validated ``scan_file`` target.

    ``path`` is the resolved file, ``base`` the directory the read is bound
    to (the resolved root, or the filesystem anchor when no root is set),
    ``identity`` the ``(st_dev, st_ino)`` seen at validation time, and
    ``base_identity`` the pinned root's identity (``None`` without a root).
    """

    path: Path
    base: Path
    identity: Tuple[int, int]
    max_bytes: int
    base_identity: Optional[Tuple[int, int]] = None


def _size_cap_message(max_bytes: int) -> str:
    return f"file_path exceeds the {max_bytes}-byte size cap"


def _validate_scan_path(
    path_str: str, *, require_root: bool, file_root: Optional[FileRoot]
) -> Tuple[Optional[str], Optional[_ScanTarget]]:
    """Return ``(error, None)`` if the path is unsafe, else ``(None, target)``.

    Rejects: any path when ``require_root`` is set but
    ``SEPTUM_MCP_FILE_ROOT`` is not; paths outside that root (when set);
    symlinks (an attacker could plant one inside the root that points
    to ``~/.ssh/id_rsa``); dotfiles and anything under a dot-directory
    (``.env``, ``.ssh/``, ``.git/``); and files larger than
    ``SEPTUM_MCP_MAX_FILE_BYTES``. With a root set, only components
    below the root are checked for a leading dot, so the operator may
    place the root itself anywhere.

    The lexical (``..``-collapsed) path is only a pre-filter that refuses
    obvious escapes before the filesystem is touched. The authoritative
    checks run on the caller's raw path resolved by the kernel's rules
    (symlinks before ``..``), and the caller must read only the returned
    resolved path: ``root/link/../x`` is judged by where it really leads,
    not by the ``root/x`` its spelling suggests. Resolution is a separate
    walk from the read, so :func:`_read_scan_target` re-walks the resolved
    path from ``base`` on descriptors without following any symlink; a
    directory swapped for a symlink in between fails the read rather
    than leaving the root, and the root descriptor itself must match the
    identity pinned in ``file_root``. Not-found, outside-root,
    dotfile and unreadable all share one message that names neither the
    path nor the root.

    ``file_root`` is the root the server pinned at startup (``None`` = no
    root configured).
    """
    if file_root is None and require_root:
        return (
            "scan_file is disabled on network transports until "
            "SEPTUM_MCP_FILE_ROOT is set"
        ), None

    try:
        raw = os.path.expanduser(path_str)
        candidate = _lexical_path(path_str)
    except (ValueError, OSError):
        return "file_path is malformed", None

    checked = (
        candidate
        if file_root is None
        else _relative_to_any(candidate, (file_root.lexical, file_root.resolved))
    )
    if checked is None or _has_dot_part(checked):
        return _SCAN_PATH_UNAVAILABLE, None

    try:
        resolved = Path(raw).resolve(strict=True)
        is_link = os.path.islink(raw)
    except (OSError, RuntimeError, ValueError):
        return _SCAN_PATH_UNAVAILABLE, None

    if is_link:
        return "scan_file refuses to follow symlinks", None

    base = Path(resolved.anchor) if file_root is None else file_root.resolved
    checked = _relative_to_any(resolved, (base,))
    if checked is None or _has_dot_part(checked):
        return _SCAN_PATH_UNAVAILABLE, None

    try:
        info = resolved.stat()
    except OSError:
        return _SCAN_PATH_UNAVAILABLE, None
    max_bytes = _scan_file_max_size()
    if info.st_size > max_bytes:
        return _size_cap_message(max_bytes), None
    return None, _ScanTarget(
        resolved,
        base,
        (info.st_dev, info.st_ino),
        max_bytes,
        None if file_root is None else file_root.identity,
    )


def _read_scan_target(target: _ScanTarget) -> FileReadResult:
    """Read ``target`` through a symlink-free descriptor walk from its base."""
    return read_file_beneath(
        target.base,
        target.path.relative_to(target.base).parts,
        max_bytes=target.max_bytes,
        expected_identity=target.identity,
        root_identity=target.base_identity,
    )


def _read_error_message(result: FileReadResult, max_bytes: int) -> str:
    """Map a reader failure to a client-safe message (no path, no parser text)."""
    if result.code == READ_UNSUPPORTED:
        return result.error
    if result.code == READ_TOO_LARGE:
        return _size_cap_message(max_bytes)
    if result.code in (READ_NOT_FOUND, READ_NOT_REGULAR, READ_CHANGED):
        return _SCAN_PATH_UNAVAILABLE
    return _SCAN_READ_FAILED


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
        # logger.exception would write the full traceback (with locals
        # like ``raw_text``) to stderr — which MCP clients capture and
        # persist (e.g. ~/Library/Logs/Claude/...). Log only the
        # exception class name and return a generic envelope so neither
        # the log nor the response leaks the input back to the caller.
        logger.error("mask_text failed: %s", type(exc).__name__)
        return _err("mask_text failed")

    _ledger_for(engine).record(engine, result.session_id, result.masked_text)
    return _ok(
        {
            "masked_text": result.masked_text,
            "session_id": result.session_id,
            "entity_count": result.entity_count,
            "entity_type_counts": result.entity_type_counts,
        }
    )


def _short_aliases(placeholders: Iterable[str]) -> Dict[str, str]:
    """Map each short alias the core unmasker honours to its full placeholder.

    Reads the alias rules from :class:`Unmasker` so the count here cannot
    drift from what ``engine.unmask`` actually restores. A short form that
    is itself a mapped placeholder is not an alias, matching the unmasker.
    """
    known = set(placeholders)
    aliases: Dict[str, str] = {}
    for placeholder in known:
        for pattern, short_prefix in Unmasker._PLACEHOLDER_SHORT_ALIASES:
            match = pattern.match(placeholder)
            if match:
                short_form = f"[{short_prefix}_{match.group(1)}]"
                if short_form not in known:
                    aliases[short_form] = placeholder
                break
    return aliases


def _placeholders_restored_by(
    text: str, emitted: FrozenSet[str], all_placeholders: Iterable[str]
) -> FrozenSet[str]:
    """Return the emitted placeholders ``engine.unmask`` would restore in ``text``.

    One regex pass over ``text`` instead of one unmask per placeholder.
    """
    tokens = set(_BRACKET_TOKEN.findall(text))
    hits = {token for token in tokens if token in emitted}
    for short_form, placeholder in _short_aliases(all_placeholders).items():
        if placeholder in emitted and short_form in tokens:
            hits.add(placeholder)
    return frozenset(hits)


def unmask_response(
    engine: SeptumEngine,
    *,
    text: Any,
    session_id: Any,
    max_placeholders: int = DEFAULT_MAX_UNMASK_PLACEHOLDERS,
) -> Dict[str, Any]:
    """Restore original values in an LLM response using ``session_id``.

    Only placeholders this session emitted in a ``mask_text`` or
    ``scan_file(mask=True)`` result are restored; any other placeholder
    stays literal. ``max_placeholders`` caps how many distinct
    placeholders a session may have restored **across all calls**:
    a call that would push that set above the cap fails and restores
    nothing, while re-restoring an already-restored placeholder is free.
    Splitting a reply across calls therefore cannot drain the map.

    The unmask itself is limited to the charged set, not the whole
    emitted set: the core unmasker replaces sequentially, so a restored
    original that happens to spell another placeholder would otherwise
    be restored too without ever being counted.
    """
    raw_text = _require_string(text, "text")
    if raw_text is None:
        return _err("'text' must be a non-empty string.")
    sid = _require_string(session_id, "session_id")
    if sid is None:
        return _err("'session_id' must be a non-empty string.")

    mapping = engine.get_session_map(sid)
    if mapping is None:
        return _err(
            f"Session '{sid}' was not found or has expired. "
            "Call mask_text again before unmasking."
        )

    ledger = _ledger_for(engine)
    emitted = ledger.emitted(sid)
    requested = _placeholders_restored_by(raw_text, emitted, mapping.values())
    would_reach = ledger.consume(sid, requested, max_placeholders)
    if would_reach is not None:
        return _err(
            f"unmask_response refused: this session would restore {would_reach} "
            f"distinct placeholders, above the per-session cap of {max_placeholders}."
        )
    try:
        restored = engine.unmask(raw_text, sid, allowed_placeholders=requested)
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("unmask_response failed: %s", type(exc).__name__)
        return _err("unmask_response failed")

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
        logger.error("detect_pii failed: %s", type(exc).__name__)
        return _err("detect_pii failed")

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
    require_root: bool = False,
    file_root: Optional[FileRoot],
) -> Dict[str, Any]:
    """Read a file into text and run either detection or full masking.

    ``require_root`` makes the call fail closed when
    ``SEPTUM_MCP_FILE_ROOT`` is unset; the server sets it on every
    network transport. ``file_root`` is the root pinned at server
    startup (``None`` = no root).

    When ``mask`` is false (the default) the file is analysed in
    detect-only mode: the session is released immediately so no PII
    values are retained. When ``mask`` is true the full masked text
    and a ``session_id`` are returned so the caller can unmask a
    downstream LLM response later.
    """
    path = _require_string(file_path, "file_path")
    if path is None:
        return _err("'file_path' must be a non-empty string.")

    rejection, target = _validate_scan_path(
        path, require_root=require_root, file_root=file_root
    )
    if target is None:
        return _err(rejection or _SCAN_PATH_UNAVAILABLE)

    read_result = _read_scan_target(target)
    if not read_result.ok:
        return _err(_read_error_message(read_result, target.max_bytes))
    if not read_result.text.strip():
        return _err("file_path is empty or contains no extractable text.")

    do_mask = bool(mask)
    lang = _require_string(language, "language") or "en"
    try:
        result = engine.mask(read_result.text, language=lang)
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("scan_file mask failed: %s", type(exc).__name__)
        return _err("scan_file failed")

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
        _ledger_for(engine).record(engine, result.session_id, result.masked_text)
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
        entity_types = entity_types_for(reg_id)
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


SUPPORTED_FILE_EXTENSIONS: tuple[str, ...] = SUPPORTED_EXTENSIONS
