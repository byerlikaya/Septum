"""Device selection utilities for the NER model registry.

Selects the preferred PyTorch backend for the current host. The import
is deferred to function call time so septum-core can be imported on
machines without a torch install (for example, the pure detection
validator flows in the gateway package).
"""

from __future__ import annotations

from typing import Literal

DeviceType = Literal["mps", "cuda", "cpu"]


def get_device() -> DeviceType:
    """Return the preferred Torch device identifier for the current host.

    The function prefers GPU-accelerated backends when available:
    1. Apple Metal Performance Shaders (MPS) on Apple Silicon.
    2. NVIDIA CUDA.
    3. CPU as a safe fallback.

    Uses getattr for MPS so that environments without the backend do not raise.
    """
    import torch

    mps_backend = getattr(torch.backends, "mps", None)
    if mps_backend is not None and mps_backend.is_available():
        return "mps"

    if torch.cuda.is_available():
        return "cuda"

    return "cpu"
