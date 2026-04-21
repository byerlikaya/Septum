#!/usr/bin/env bash

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SETUP_MODE=false
RESET_MODE=false
for arg in "$@"; do
  case "$arg" in
    --setup) SETUP_MODE=true ;;
    --reset) RESET_MODE=true ;;
  esac
done

if [[ "$RESET_MODE" == true ]]; then
  echo "[reset] Wiping all local data (database, uploads, indexes, config) ..."
  # Kill any still-running dev servers BEFORE deleting DB files — otherwise
  # aiosqlite worker threads keep the SQLite handle open and the new uvicorn
  # fails with "disk I/O error" on the first PRAGMA. Also kill Next.js so
  # the cache wipe below doesn't fight with a running build watcher.
  pkill -f "uvicorn septum_api.main:app" >/dev/null 2>&1 || true
  pkill -f "next dev" >/dev/null 2>&1 || true
  sleep 1
  rm -f  "$PROJECT_ROOT/config.json"
  # Also strip SQLite sidecar files (WAL + shared-memory) so a stale
  # transaction log doesn't survive the reset.
  rm -f  "$PROJECT_ROOT/septum.db" "$PROJECT_ROOT/septum.db-wal" "$PROJECT_ROOT/septum.db-shm"
  # Legacy copy that predates the Phase 8 working-dir change — older
  # dev.sh revisions started uvicorn from packages/api, which created
  # septum.db there. New dev.sh starts from PROJECT_ROOT, but the old
  # file can still linger and confuse the next run.
  rm -f  "$PROJECT_ROOT/packages/api/septum.db" \
         "$PROJECT_ROOT/packages/api/septum.db-wal" \
         "$PROJECT_ROOT/packages/api/septum.db-shm"
  rm -rf "$PROJECT_ROOT/uploads"
  rm -rf "$PROJECT_ROOT/anon_maps"
  rm -rf "$PROJECT_ROOT/vector_indexes"
  rm -rf "$PROJECT_ROOT/bm25_indexes"
  rm -rf "$PROJECT_ROOT/documents"
  rm -rf "$PROJECT_ROOT/data"
  # Webpack/SWC caches reference file paths by id — when a source file is
  # deleted (e.g. the orphan-sweep above) the dev server keeps trying to
  # serve the old chunk and the browser hits ChunkLoadError on every
  # reload. Nuke the cache so the next boot rebuilds cleanly.
  rm -rf "$PROJECT_ROOT/packages/web/.next"
  rm -rf "$PROJECT_ROOT/packages/web/.turbo"
  rm -rf "$PROJECT_ROOT/packages/web/node_modules/.cache"
  echo "[reset] Done — next start will launch the setup wizard."
fi

echo "Project root: $PROJECT_ROOT"

export SEPTUM_CONFIG_PATH="${SEPTUM_CONFIG_PATH:-$PROJECT_ROOT/config.json}"
# Placeholder for the dashboard footer; release images inject the real
# version via the Docker publish workflow's build-args.
export NEXT_PUBLIC_APP_VERSION="${NEXT_PUBLIC_APP_VERSION:-0.0.0-dev}"

find_available_port() {
  local port=$1
  while lsof -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1; do
    echo "Port $port is in use, trying $((port + 1))..." >&2
    port=$((port + 1))
  done
  echo "$port"
}

if [[ "$SETUP_MODE" == true ]]; then
  echo "[setup] Installing / upgrading modular packages in editable mode ..."
  (
    cd "$PROJECT_ROOT"
    python -m pip install --upgrade pip
    # septum-core needs the [transformers] extra for the NER pipeline used
    # by the detector; [test] pulls in pytest deps for the core tests.
    python -m pip install --upgrade -e "packages/core[transformers,test]"
    python -m pip install --upgrade -e "packages/queue[redis,test]"
    # septum-api needs every optional extra so the full dev stack (auth,
    # rate-limit, postgres, uvicorn) works out of the box.
    python -m pip install --upgrade -e "packages/api[auth,rate-limit,postgres,server,test]"
    python -m pip install --upgrade -e "packages/mcp[test]"
    python -m pip install --upgrade -e "packages/gateway[server,test]"
    python -m pip install --upgrade -e "packages/audit[queue,server,test]"
  )

  echo "[setup] Installing / upgrading heavy ML / ingestion dependencies (packages/api/requirements.txt) ..."
  # packages/api/requirements.txt is still the single source of truth for
  # ML / OCR / Whisper / ingestion deps that the services/ modules import.
  # Package pyprojects declare only the narrow FastAPI/SQLAlchemy surface.
  (
    cd "$PROJECT_ROOT/packages/api"
    python -m pip install --upgrade -r requirements.txt
  )

  echo "[setup] Installing / upgrading frontend dependencies (npm install) ..."
  (
    cd "$PROJECT_ROOT/packages/web"
    npm install
  )

  echo "[setup] Done. Run ./dev.sh to start the dev servers."
fi

BACKEND_PORT=$(find_available_port "${BACKEND_PORT:-8000}")
FRONTEND_PORT=$(find_available_port "${FRONTEND_PORT:-3000}")

export BACKEND_INTERNAL_URL="http://localhost:$BACKEND_PORT"

(
  cd "$PROJECT_ROOT"
  # Avoid OMP Error #179 (pthread_mutex_init) on macOS with PyTorch/MPS
  export OMP_NUM_THREADS=1
  python -m uvicorn septum_api.main:app --reload --host 0.0.0.0 --port "$BACKEND_PORT"
) &

BACKEND_PID=$!

cd "$PROJECT_ROOT/packages/web"

echo "Septum starting on http://localhost:$FRONTEND_PORT ..."
PORT=$FRONTEND_PORT npm run dev

echo "Stopping backend process (PID: $BACKEND_PID) ..."
kill "$BACKEND_PID" 2>/dev/null || true
