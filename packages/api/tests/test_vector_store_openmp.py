"""Regression test: embedding and FAISS search must share one process on macOS.

The macOS torch and faiss wheels each bundle a libomp; without the guard in
``septum_api.services.vector_store`` the first multi-threaded encode after
faiss is loaded kills the interpreter with SIGSEGV. A crash cannot be caught
in-process, so each scenario runs in a fresh subprocess. faiss must reach the
process through that module: imported before it, faiss's libomp loads first and
the guard can no longer pick the load order.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

from septum_api.services.vector_store import _DEFAULT_ST_MODEL

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="the duplicate libomp only ships in macOS wheels")

_SCENARIO = textwrap.dedent(
    """
    import sys

    if sys.argv[1] == "torch-first":
        import torch

    from septum_api.services.vector_store import get_shared_sentence_transformer

    import faiss
    import numpy as np

    model = get_shared_sentence_transformer()
    texts = [f"invoice {i} for the customer account" for i in range(256)]
    vectors = model.encode(texts, batch_size=32, normalize_embeddings=True).astype("float32")
    index = faiss.IndexIDMap(faiss.IndexFlatIP(vectors.shape[1]))
    index.add_with_ids(vectors, np.arange(len(vectors)))
    _, ids = index.search(vectors[:4], 1)
    assert ids[:, 0].tolist() == [0, 1, 2, 3], ids
    model.encode(texts, batch_size=32)
    """
)


def _model_cached() -> bool:
    from huggingface_hub import try_to_load_from_cache

    return isinstance(try_to_load_from_cache(_DEFAULT_ST_MODEL, "config.json"), str)


@pytest.mark.parametrize("order", ["vector-store-first", "torch-first"])
def test_encode_and_faiss_search_do_not_crash(order: str) -> None:
    """A real encode, a FAISS search and a second encode exit cleanly in either import order."""
    if not _model_cached():
        pytest.skip(f"{_DEFAULT_ST_MODEL} is not in the local Hugging Face cache")
    env = {k: v for k, v in os.environ.items() if k not in ("OMP_NUM_THREADS", "KMP_DUPLICATE_LIB_OK")}
    env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    result = subprocess.run(
        [sys.executable, "-c", _SCENARIO, order],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"rc={result.returncode}\n{result.stderr[-2000:]}"


def test_user_thread_count_above_one_is_warned() -> None:
    """A pre-set OMP_NUM_THREADS above 1 is kept but logged, since it re-opens the crash."""
    env = dict(os.environ, OMP_NUM_THREADS="4", HF_HUB_OFFLINE="1")
    result = subprocess.run(
        [sys.executable, "-c", "import septum_api.services.vector_store"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert "OMP_NUM_THREADS=4 on macOS" in result.stderr
