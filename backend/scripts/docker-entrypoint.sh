#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Septum Docker Entrypoint
# Validates environment, runs migrations, starts the application.
# ---------------------------------------------------------------------------

log() { echo "[entrypoint] $*"; }

# --- Auto-generate ENCRYPTION_KEY if not set ---
if [ -z "${ENCRYPTION_KEY:-}" ]; then
  ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
  export ENCRYPTION_KEY
  log "ENCRYPTION_KEY auto-generated (not persisted — set it in .env for stable encryption across restarts)"
fi

# --- Validate at least one LLM provider key is available ---
if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -z "${OPENAI_API_KEY:-}" ] && [ "${LLM_PROVIDER:-}" != "ollama" ]; then
  log "WARNING: No ANTHROPIC_API_KEY or OPENAI_API_KEY set and LLM_PROVIDER is not 'ollama'."
  log "         Chat functionality will not work until an API key is configured via Settings UI."
fi

# --- Run database migrations ---
if [ -n "${DATABASE_URL:-}" ]; then
  log "Running Alembic migrations..."
  python -m alembic upgrade head
else
  log "No DATABASE_URL set — using SQLite with auto-init"
fi

# --- Seed defaults (idempotent) ---
log "Seeding defaults..."
python -c "
import asyncio
from app.database import init_db
asyncio.run(init_db())
"

# --- Start application ---
log "Starting Septum backend..."
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
