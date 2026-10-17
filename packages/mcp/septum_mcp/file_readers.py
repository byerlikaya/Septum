from __future__ import annotations

"""
Lightweight file readers for the ``scan_file`` MCP tool.

This module intentionally keeps the dependency surface small: only
pure-Python parsers are used (``pypdf``, ``python-docx``) plus the
standard library. Heavy extractors (OCR via PaddleOCR/Tesseract, audio
via Whisper, video, scanned PDF pages) are out of scope for the MCP
server — they belong in the full septum-api pipeline where the cost
of loading multi-gigabyte models is acceptable.

Content-based MIME detection is *not* used here because it would pull
in ``python-magic`` (libmagic system dependency). The MCP tool is
invoked from editors that already know the file's extension, so
extension-based dispatch is enough for this entrypoint.

All readers return :class:`FileReadResult` objects so the caller can
distinguish "format unsupported" from "file missing" from "file was
read successfully but is empty".
"""

import csv
import io
import json
import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS: tuple[str, ...] = (
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".json",
    ".pdf",
    ".docx",
)

READ_NOT_FOUND = "not_found"
READ_NOT_REGULAR = "not_regular"
READ_UNSUPPORTED = "unsupported"
READ_TOO_LARGE = "too_large"
READ_CHANGED = "changed"
READ_PARSE_ERROR = "parse_error"

_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


@dataclass
class FileReadResult:
    """Outcome of a :func:`read_file` call.

    ``ok=False`` means the file could not be turned into text. ``code``
    is one of the ``READ_*`` constants; ``error`` is a fixed message that
    never contains the path or parser exception text, so it is safe to
    surface to an MCP client.
    """

    ok: bool
    text: str = ""
    format: str = ""
    error: str = ""
    code: str = ""


def _failure(code: str, error: str, fmt: str = "") -> FileReadResult:
    return FileReadResult(ok=False, format=fmt, error=error, code=code)


def _not_regular() -> FileReadResult:
    return _failure(READ_NOT_REGULAR, "Not a regular file")


def read_file(
    path: str | Path,
    *,
    max_bytes: Optional[int] = None,
    expected_identity: Optional[Tuple[int, int]] = None,
) -> FileReadResult:
    """Read ``path`` and return its textual content.

    Format dispatch is based on the file extension. The file is opened
    once with ``O_NOFOLLOW`` (a symlinked final component is refused) and
    ``O_NONBLOCK`` (a FIFO cannot stall the call); every later check runs
    on that descriptor (see :func:`_read_descriptor`). Intermediate
    components are followed — use :func:`read_file_beneath` when the read
    must stay inside a directory.
    """
    file_path = Path(path)
    try:
        fd = os.open(file_path, os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK)
    except FileNotFoundError:
        return _failure(READ_NOT_FOUND, "File not found")
    except OSError:
        return _not_regular()
    return _read_descriptor(
        fd, file_path.suffix, max_bytes=max_bytes, expected_identity=expected_identity
    )


def read_file_beneath(
    root: str | Path,
    parts: Sequence[str],
    *,
    max_bytes: Optional[int] = None,
    expected_identity: Optional[Tuple[int, int]] = None,
    root_identity: Optional[Tuple[int, int]] = None,
) -> FileReadResult:
    """Read ``root/parts...`` without following any symlink below ``root``.

    ``root`` is opened once as a directory, then each component is opened
    relative to its parent's descriptor with ``O_NOFOLLOW``, so swapping
    any directory under ``root`` for a symlink mid-call makes the open
    fail instead of leaving ``root``. ``.``, ``..`` and empty components
    are refused. The leaf is ``lstat``-ed through its parent descriptor
    and anything other than a regular file is refused before it is opened.
    When ``root_identity`` is given, the opened root descriptor must carry
    that ``(st_dev, st_ino)``: a root path replaced (for instance by a
    symlink) after the identity was recorded is refused, not followed.
    """
    if not parts or any(_bad_component(part) for part in parts):
        return _not_regular()
    if os.open not in os.supports_dir_fd:
        if root_identity is not None and _path_identity(root) != root_identity:
            return _not_regular()
        return read_file(
            Path(root, *parts), max_bytes=max_bytes, expected_identity=expected_identity
        )
    try:
        fd = _open_beneath(root, parts, root_identity)
    except FileNotFoundError:
        return _failure(READ_NOT_FOUND, "File not found")
    except OSError:
        return _not_regular()
    if fd is None:
        return _not_regular()
    return _read_descriptor(
        fd, Path(parts[-1]).suffix, max_bytes=max_bytes, expected_identity=expected_identity
    )


def _path_identity(path: str | Path) -> Optional[Tuple[int, int]]:
    try:
        info = os.stat(path)
    except OSError:
        return None
    return info.st_dev, info.st_ino


def _bad_component(part: str) -> bool:
    return (
        part in ("", ".", "..")
        or os.sep in part
        or bool(os.altsep and os.altsep in part)
    )


def _open_beneath(
    root: str | Path, parts: Sequence[str], root_identity: Optional[Tuple[int, int]]
) -> Optional[int]:
    """Return an fd for the regular file ``root/parts...``, or ``None`` if it is not one.

    ``None`` is also returned when ``root_identity`` is set and the opened
    root directory is not the one it names.
    """
    dir_fd = os.open(root, os.O_RDONLY | _O_DIRECTORY)
    try:
        if root_identity is not None:
            info = os.fstat(dir_fd)
            if (info.st_dev, info.st_ino) != root_identity:
                return None
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | _O_NOFOLLOW | _O_DIRECTORY, dir_fd=dir_fd)
            os.close(dir_fd)
            dir_fd = next_fd
        leaf = parts[-1]
        if not stat.S_ISREG(os.stat(leaf, dir_fd=dir_fd, follow_symlinks=False).st_mode):
            return None
        return os.open(leaf, os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK, dir_fd=dir_fd)
    finally:
        os.close(dir_fd)


def _read_descriptor(
    fd: int,
    extension: str,
    *,
    max_bytes: Optional[int],
    expected_identity: Optional[Tuple[int, int]],
) -> FileReadResult:
    """Validate and parse the open file ``fd``; always closes it.

    Every check — regular file with a single link (a hardlink could alias
    a file outside the allowed tree), ``(st_dev, st_ino)`` matching
    ``expected_identity``, size within ``max_bytes`` — runs on ``fd``, so
    the bytes parsed are the bytes checked.
    """
    try:
        info = os.fstat(fd)
    except OSError:
        info = None
    if info is None or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd)
        return _not_regular()
    with os.fdopen(fd, "rb") as handle:
        extension = extension.lower()
        reader = _EXTENSION_READERS.get(extension)
        if reader is None:
            supported = ", ".join(SUPPORTED_EXTENSIONS)
            return _failure(
                READ_UNSUPPORTED,
                f"Unsupported file type. septum-mcp supports: {supported}",
            )
        fmt = extension.lstrip(".")
        if expected_identity is not None and (info.st_dev, info.st_ino) != expected_identity:
            return _failure(READ_CHANGED, "File changed during read")
        if max_bytes is not None and info.st_size > max_bytes:
            return _failure(READ_TOO_LARGE, "File exceeds the size cap")
        data = handle.read() if max_bytes is None else handle.read(max_bytes + 1)
    if max_bytes is not None and len(data) > max_bytes:
        return _failure(READ_TOO_LARGE, "File exceeds the size cap")

    try:
        text = reader(data)
    except Exception as exc:
        logger.warning("file reader failed for %s: %s", fmt, type(exc).__name__)
        return _failure(READ_PARSE_ERROR, f"Failed to read {fmt} file", fmt)

    return FileReadResult(ok=True, text=text, format=fmt)


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _read_csv(data: bytes) -> str:
    """Return CSV content as tab-joined lines for detector-friendly input."""
    reader = csv.reader(io.StringIO(_decode(data), newline=""))
    return "\n".join("\t".join(row) for row in reader)


def _read_json(data: bytes) -> str:
    """Pretty-print JSON so string values remain detectable by the pipeline."""
    raw = _decode(data)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    buffer = io.StringIO()
    json.dump(parsed, buffer, ensure_ascii=False, indent=2)
    return buffer.getvalue()


def _read_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return "\n".join(pages)


def _read_docx(data: bytes) -> str:
    import docx

    document = docx.Document(io.BytesIO(data))
    parts: list[str] = [p.text for p in document.paragraphs if p.text]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text for cell in row.cells if cell.text]
            if cells:
                parts.append("\t".join(cells))
    return "\n".join(parts)


_EXTENSION_READERS: Dict[str, Callable[[bytes], str]] = {
    ".txt": _decode,
    ".md": _decode,
    ".markdown": _decode,
    ".csv": _read_csv,
    ".json": _read_json,
    ".pdf": _read_pdf,
    ".docx": _read_docx,
}
