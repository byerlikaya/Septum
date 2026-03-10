#!/usr/bin/env bash

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SETUP_MODE=false
if [[ "${1:-}" == "--setup" ]]; then
  SETUP_MODE=true
fi

echo "Project root: $PROJECT_ROOT"

if [[ "$SETUP_MODE" == true ]]; then
  echo "[setup] Installing / upgrading backend dependencies (pip install -r backend/requirements.txt) ..."
  (
    cd "$PROJECT_ROOT/backend"
    python -m pip install --upgrade pip
    python -m pip install --upgrade -r requirements.txt
  )

  echo "[setup] Installing / upgrading frontend dependencies (npm install) ..."
  (
    cd "$PROJECT_ROOT/frontend"
    npm install
  )

  echo "[setup] Done. You can now run ./dev.sh to start only the dev servers without re-installing dependencies."
fi

(
  cd "$PROJECT_ROOT/backend"
  # Avoid OMP Error #179 (pthread_mutex_init) on macOS with PyTorch/MPS
  export OMP_NUM_THREADS=1
  echo "Starting backend on http://localhost:8000 ..."
  python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
) &

BACKEND_PID=$!

cd "$PROJECT_ROOT/frontend"

echo "Starting frontend on http://localhost:3000 ..."
npm run dev

echo "Stopping backend process (PID: $BACKEND_PID) ..."
kill "$BACKEND_PID" 2>/dev/null || true

