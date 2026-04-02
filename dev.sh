#!/usr/bin/env bash

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SETUP_MODE=false
if [[ "${1:-}" == "--setup" ]]; then
  SETUP_MODE=true
fi

echo "Project root: $PROJECT_ROOT"

if [[ -f "$PROJECT_ROOT/.env" ]]; then
  # Load shared environment variables for both backend and frontend
  set -a
  # shellcheck disable=SC1090
  source "$PROJECT_ROOT/.env"
  set +a
fi

find_available_port() {
  local port=$1
  while lsof -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1; do
    echo "Port $port is in use, trying $((port + 1))..." >&2
    port=$((port + 1))
  done
  echo "$port"
}

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

BACKEND_PORT=$(find_available_port "${BACKEND_PORT:-8000}")
FRONTEND_PORT=$(find_available_port "${FRONTEND_PORT:-3000}")

export NEXT_PUBLIC_API_URL="http://localhost:$BACKEND_PORT"

(
  cd "$PROJECT_ROOT/backend"
  # Avoid OMP Error #179 (pthread_mutex_init) on macOS with PyTorch/MPS
  export OMP_NUM_THREADS=1
  echo "Starting backend on http://localhost:$BACKEND_PORT ..."
  python -m uvicorn app.main:app --reload --host 0.0.0.0 --port "$BACKEND_PORT"
) &

BACKEND_PID=$!

cd "$PROJECT_ROOT/frontend"

echo "Starting frontend on http://localhost:$FRONTEND_PORT ..."
PORT=$FRONTEND_PORT npm run dev

echo "Stopping backend process (PID: $BACKEND_PID) ..."
kill "$BACKEND_PID" 2>/dev/null || true

