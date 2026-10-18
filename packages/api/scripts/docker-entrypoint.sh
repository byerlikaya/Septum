#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Septum Docker Entrypoint
# Ensures bootstrap config exists, runs migrations, starts the application.
# ---------------------------------------------------------------------------

log() { echo "[entrypoint] $*"; }

# --- Ensure bootstrap config.json (auto-generates keys if needed) ---
python -c "from septum_api.bootstrap import get_config; get_config()"

# --- Run database migrations (PostgreSQL only; SQLite uses create_all) ---
# A failed migration stops the container: serving on a half-migrated
# schema breaks at runtime in ways that are much harder to diagnose.
python -c "
from septum_api.bootstrap import get_config, needs_setup
config = get_config()
if config.database_url and not needs_setup():
    import subprocess, os, sys
    env = os.environ.copy()
    env['DATABASE_URL'] = config.database_url
    result = subprocess.run(
        ['python', '-m', 'alembic', 'upgrade', 'head'],
        env=env,
    )
    if result.returncode != 0:
        print(
            '[entrypoint] ERROR: database migration failed (alembic exit code '
            + str(result.returncode) + '); not starting on an outdated schema',
            file=sys.stderr,
        )
        sys.exit(1)
else:
    print('[entrypoint] No DATABASE_URL configured — using SQLite with auto-init')
"

# --- Start application ---
log "Starting Septum backend..."
exec python -m uvicorn septum_api.main:app --host 0.0.0.0 --port 8000
