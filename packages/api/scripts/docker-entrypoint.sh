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

# --- Proxy trust ---
# Only these peers may set X-Forwarded-For (the client address used for
# rate limits and error logs). Standalone: Next.js on 127.0.0.1; compose:
# the web container's address. A wildcard, or a network broader than an
# IPv4 /8 or an IPv6 /32 (0.0.0.0/0, ::/0, 0.0.0.0/00, 0.0.0.0/0.0.0.0,
# 10.0.0.0/7, ...), would let far too many clients pick their own address,
# so they are refused. Entries are parsed with Python's ipaddress, not
# pattern-matched.
FORWARDED_ALLOW_IPS="${FORWARDED_ALLOW_IPS:-127.0.0.1}"
python -c '
import ipaddress, re, sys
for entry in filter(None, re.split(r"[,\s]+", sys.argv[1])):
    if "*" in entry:
        sys.exit(1)
    try:
        network = ipaddress.ip_network(entry, strict=False)
    except ValueError:
        continue
    if network.prefixlen < (8 if network.version == 4 else 32):
        sys.exit(1)
' "$FORWARDED_ALLOW_IPS" || {
  echo "[entrypoint] ERROR: FORWARDED_ALLOW_IPS must list proxy addresses, not a wildcard or a network broader than /8 (IPv4) or /32 (IPv6)" >&2
  exit 1
}

# --- Start application ---
log "Starting Septum backend (trusted proxy: $FORWARDED_ALLOW_IPS)..."
exec python -m uvicorn septum_api.main:app --host 0.0.0.0 --port 8000 \
  --proxy-headers --forwarded-allow-ips "$FORWARDED_ALLOW_IPS"
