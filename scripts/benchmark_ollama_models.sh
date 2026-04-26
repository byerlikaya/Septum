#!/usr/bin/env bash
# Run packages/api/tests/benchmark_detection.py against several Ollama
# models in turn so the L3 (semantic) layer can be compared head-to-head.
#
# Usage:
#   ./scripts/benchmark_ollama_models.sh                 # default model set
#   ./scripts/benchmark_ollama_models.sh llama3.2:3b qwen2.5:14b
#
# Prereqs:
#   - Ollama running locally on the URL pointed to by ``ollama_base_url``
#     in your active settings (default http://localhost:11434).
#   - Each model already pulled: ``ollama pull <model>`` ahead of time.
#   - Septum API venv active and ``pytest`` installed.
#
# Output:
#   For each model: full pytest output streamed to stdout, plus a
#   compact summary line ``[model] precision recall f1`` extracted from
#   the run. Raw per-model logs land in benchmark_runs/<model>.log.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DEFAULT_MODELS=("llama3.2:3b" "aya-expanse:8b" "qwen2.5:14b")
MODELS=("${@:-${DEFAULT_MODELS[@]}}")

OUTDIR="benchmark_runs"
mkdir -p "$OUTDIR"

printf '\n=== Benchmarking %d Ollama model(s) ===\n' "${#MODELS[@]}"
printf '  %s\n' "${MODELS[@]}"
printf '\n'

for MODEL in "${MODELS[@]}"; do
  SAFE_NAME="${MODEL//[\/:]/_}"
  LOG="$OUTDIR/${SAFE_NAME}.log"

  printf '\n--- %s ---\n' "$MODEL"
  printf '  Log: %s\n' "$LOG"

  SEPTUM_BENCHMARK_OLLAMA_MODEL="$MODEL" \
    pytest packages/api/tests/benchmark_detection.py -v -s \
    2>&1 | tee "$LOG"

  # Pull the headline F1 row (Combined) out of the log for a quick
  # at-a-glance comparison.
  SUMMARY="$(grep -E '^\| \*\*Combined\*\*' "$LOG" | tail -1 || true)"
  if [[ -n "$SUMMARY" ]]; then
    printf '  → %s  %s\n' "$MODEL" "$SUMMARY"
  fi
done

printf '\n=== Done. Per-model logs in %s ===\n' "$OUTDIR"
