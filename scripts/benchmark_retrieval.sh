#!/usr/bin/env bash
# Run packages/api/tests/benchmark_retrieval.py once per configuration, then
# render the delta table against the first configuration.
#
# Usage:
#   ./scripts/benchmark_retrieval.sh                          # production embedder, pipeline chunker
#   ./scripts/benchmark_retrieval.sh MODEL[,CHUNKER] ...      # one run per configuration
#
# Each configuration is a Hugging Face model id (pinned in core model_pins.py)
# with an optional chunker (pipeline | structured | sliding, default pipeline).
#
# Prereqs:
#   - Septum API venv active, pytest installed.
#   - Each embedder already in the local Hugging Face cache (offline run).
#
# Output:
#   benchmark_runs/retrieval/<config>.json (schema v1) and .log per configuration,
#   then the delta table printed to stdout.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="$REPO_ROOT/packages/api"
OUTDIR="$REPO_ROOT/benchmark_runs/retrieval"
DEFAULT_CONFIG="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2,pipeline"

if [[ $# -eq 0 ]]; then
  set -- "$DEFAULT_CONFIG"
fi
mkdir -p "$OUTDIR"

JSON_FILES=()
for CONFIG in "$@"; do
  IFS=, read -r MODEL CHUNKER <<<"$CONFIG"
  CHUNKER="${CHUNKER:-pipeline}"
  SAFE_NAME="${MODEL//[\/:]/_}__${CHUNKER}"
  OUT_JSON="$OUTDIR/${SAFE_NAME}.json"
  LOG="$OUTDIR/${SAFE_NAME}.log"

  printf '\n--- %s (chunker: %s) ---\n' "$MODEL" "$CHUNKER"
  printf '  Log: %s\n' "$LOG"

  if ! (
    cd "$API_DIR"
    HF_HUB_OFFLINE=1 \
    SEPTUM_BENCHMARK_EMBEDDING_MODEL="$MODEL" \
    SEPTUM_BENCHMARK_CHUNKER="$CHUNKER" \
    SEPTUM_BENCHMARK_OUT="$OUT_JSON" \
      python -m pytest tests/benchmark_retrieval.py -k full_retrieval -s -q \
      2>&1 | tee "$LOG"
  ); then
    echo "error: benchmark run failed for $CONFIG; see $LOG" >&2
    exit 1
  fi
  if [[ ! -s "$OUT_JSON" ]]; then
    echo "error: $OUT_JSON was not written; see $LOG" >&2
    exit 1
  fi
  JSON_FILES+=("$OUT_JSON")
done

printf '\n=== Delta report (baseline: %s) ===\n' "${JSON_FILES[0]}"
(cd "$API_DIR" && python -m tests.retrieval_report "${JSON_FILES[@]}")
