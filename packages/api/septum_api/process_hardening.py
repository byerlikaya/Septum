"""Process-level hardening applied once, before the API loads anything else."""

from __future__ import annotations

import ctypes
import os
import sys

try:
    import resource
except ImportError:
    resource = None

_PR_SET_DUMPABLE = 4


def _mark_not_dumpable() -> bool:
    """Clear the Linux dumpable flag so no core dump or ptrace attach can read this process.

    Uses ``prctl(PR_SET_DUMPABLE, 0)`` through the C library. Returns
    ``False`` on platforms other than Linux, which have no ``prctl``. On
    Linux it fails closed: a C library without ``prctl`` or a failing call
    raises :class:`OSError`.
    """
    if not sys.platform.startswith("linux"):
        return False
    prctl = getattr(ctypes.CDLL(None, use_errno=True), "prctl", None)
    if prctl is None:
        raise OSError("prctl is not available in this C library; cannot mark the process non-dumpable.")
    prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    prctl.restype = ctypes.c_int
    if prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return True


def disable_core_dumps() -> bool:
    """Forbid core dumps for this process and the children it starts.

    A core dump would write decrypted documents, audio and anonymization maps
    held in memory to disk in plaintext. This sets ``RLIMIT_CORE`` to zero
    and, on Linux, marks the process non-dumpable.

    The non-dumpable flag does not survive ``exec``: a child that runs another
    program (ffmpeg, the spawned OCR worker's interpreter) keeps only the
    inherited zero ``RLIMIT_CORE``, so the OCR worker calls this function
    again as its pool initializer. A host whose ``core_pattern`` pipes cores
    to a handler can ignore ``RLIMIT_CORE``; there, only the dumpable flag
    protects a process.

    Returns ``False`` on platforms without the :mod:`resource` module
    (Windows), where there is nothing to set.
    """
    if resource is None:
        return False
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    _mark_not_dumpable()
    return True
