"""Test helper that records every attempt to write a file while code under test runs."""

from __future__ import annotations

import builtins
import io
import os
import tempfile
from typing import Any, List

import pytest

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
_TEMPFILE_FACTORIES = (
    "NamedTemporaryFile",
    "TemporaryFile",
    "mkstemp",
    "mkdtemp",
)


def install_write_guard(monkeypatch: pytest.MonkeyPatch) -> List[str]:
    """Record every file write attempt from here on and return the record.

    Covers write-mode ``open``/``io.open``, ``os.open`` with a write flag and
    the :mod:`tempfile` factories (a spooled file rolling over to disk goes
    through ``tempfile.TemporaryFile``). Wrapping an already open descriptor
    (``open(fd)``, as :mod:`subprocess` does for its pipes) creates nothing
    and is not recorded, nor is opening ``os.devnull``. Attempts are recorded rather than
    raised so code that degrades on an optional step cannot hide them.

    Limits: the guard only sees Python-level calls. Writes made from C code
    (PyMuPDF, libmagic, OpenSSL) and from the ffmpeg child process are
    invisible to it; the ffmpeg child is protected only by the argument list
    it is given (pipes, no output file), which the guard cannot verify.
    """

    attempts: List[str] = []
    real_open = builtins.open
    real_os_open = os.open

    def guarded_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if not isinstance(file, int) and any(flag in mode for flag in ("w", "a", "x", "+")):
            attempts.append(f"open({file!r}, {mode!r})")
        return real_open(file, mode, *args, **kwargs)

    def guarded_os_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if flags & _WRITE_FLAGS and path != os.devnull:
            attempts.append(f"os.open({path!r})")
        return real_os_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(io, "open", guarded_open)
    monkeypatch.setattr(os, "open", guarded_os_open)
    for name in _TEMPFILE_FACTORIES:
        real_factory = getattr(tempfile, name)

        def guarded_factory(*args: Any, _name: str = name, _real: Any = real_factory, **kwargs: Any) -> Any:
            attempts.append(f"tempfile.{_name}")
            return _real(*args, **kwargs)

        monkeypatch.setattr(tempfile, name, guarded_factory)
    return attempts
