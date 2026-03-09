"""Device selection utilities for Septum.

This module provides a single helper to select the most appropriate
PyTorch device on the current machine. The selection order is:
MPS (Apple Silicon) → CUDA → CPU.
"""

from __future__ import annotations

from typing import Literal

import torch

DeviceType = Literal["mps", "cuda", "cpu"]


def get_device() -> DeviceType:
    """Return the preferred Torch device identifier for the current host.

    The function prefers GPU-accelerated backends when available:
    1. Apple Metal Performance Shaders (MPS) on Apple Silicon.
    2. NVIDIA CUDA.
    3. CPU as a safe fallback.
    """
    # Some environments may not expose the mps backend; guard with getattr.
    mps_backend = getattr(torch.backends, "mps", None)
    if mps_backend is not None and mps_backend.is_available():
        return "mps"

    if torch.cuda.is_available():
        return "cuda"

    return "cpu"

