#!/usr/bin/env bash

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Project root: $PROJECT_ROOT"

echo "Starting backend on http://localhost:8000 ..."
(
  cd "$PROJECT_ROOT/backend"
  python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
) &

BACKEND_PID=$!

echo "Starting frontend on http://localhost:3000 ..."
cd "$PROJECT_ROOT/frontend"

if [ ! -d "node_modules" ]; then
  echo "Installing npm dependencies (npm install) ..."
  npm install
fi

npm run dev

echo "Stopping backend process (PID: $BACKEND_PID) ..."
kill "$BACKEND_PID" 2>/dev/null || true

