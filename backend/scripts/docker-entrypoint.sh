#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Septum Docker Entrypoint
# Ensures bootstrap config exists, runs migrations, starts the application.
# ---------------------------------------------------------------------------

log() { echo "[entrypoint] $*"; }

# --- Ensure bootstrap config.json (auto-generates keys if needed) ---
python -c "from app.bootstrap import get_config; get_config()"

# --- Run database migrations (PostgreSQL only; SQLite uses create_all) ---
python -c "
from app.bootstrap import get_config, needs_setup
config = get_config()
if config.database_url and not needs_setup():
    import subprocess, os
    env = os.environ.copy()
    env['DATABASE_URL'] = config.database_url
    result = subprocess.run(
        ['python', '-m', 'alembic', 'upgrade', 'head'],
        env=env,
    )
    if result.returncode != 0:
        print('[entrypoint] WARNING: Alembic migration failed')
else:
    print('[entrypoint] No DATABASE_URL configured — using SQLite with auto-init')
"

# --- Start application ---
log "Starting Septum backend..."
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
