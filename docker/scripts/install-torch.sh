#!/bin/sh
# Install PyTorch with the wheel matching $TORCH_VARIANT (cpu | gpu).
# Single source of truth for the torch pin across api + standalone
# Dockerfiles — bumping the version is a one-file change here.

set -eu

VARIANT="${1:-cpu}"
TORCH_PIN="torch==2.10.0"

case "$VARIANT" in
  gpu)
    # Full wheel from PyPI default index; includes CUDA shared libs.
    pip install --no-warn-script-location "$TORCH_PIN"
    ;;
  cpu)
    # CPU-only wheel; excludes CUDA / nvidia / triton (saves ~6 GB).
    # The torch==2.10.0 pin in requirements.txt matches both wheels so
    # the follow-up pip install -r is a no-op for torch (PEP 440
    # ignores the +cpu local version suffix).
    pip install --no-warn-script-location \
      "$TORCH_PIN" --index-url https://download.pytorch.org/whl/cpu
    ;;
  *)
    echo "install-torch.sh: unknown TORCH_VARIANT=$VARIANT (expected 'cpu' or 'gpu')" >&2
    exit 1
    ;;
esac
