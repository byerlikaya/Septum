# -----------------------------------------------------------------------------
# Septum — combined backend + frontend image
#
# Single-container deployment for quick evaluation. Uses SQLite (no external
# PostgreSQL or Redis required). For production, use docker-compose.yml with
# separate backend/frontend images and PostgreSQL + Redis.
#
#   docker run -p 3000:3000 -p 8000:8000 byerlikaya/septum
# -----------------------------------------------------------------------------

# ── Stage 1: build Python backend dependencies ──
FROM python:3.12-slim AS backend-builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libmagic1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN python -m venv /build/.venv
ENV PATH="/build/.venv/bin:$PATH"

COPY backend/requirements.txt .
RUN pip install --no-warn-script-location -r requirements.txt

# ── Stage 2: build Next.js frontend ──
FROM node:20-alpine AS frontend-builder

WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install

COPY frontend/ .
RUN mkdir -p public
ENV NEXT_PUBLIC_API_URL=http://localhost:8000
RUN npm run build

# ── Stage 3: combined runtime ──
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/backend/.venv/bin:$PATH" \
    NODE_ENV=production \
    DB_PATH=/app/data/septum.db \
    DOCUMENT_STORAGE_DIR=/app/uploads \
    ANON_MAP_STORAGE_DIR=/app/anon_maps

# Install runtime dependencies + Node.js
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libmagic1 ffmpeg curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 septum \
    && useradd --uid 1000 --gid septum --shell /bin/sh --create-home septum

WORKDIR /app

# Copy backend
COPY --from=backend-builder /build/.venv /app/backend/.venv
COPY --chown=septum:septum backend/app/ /app/backend/app/
COPY --chown=septum:septum backend/scripts/docker-entrypoint.sh /app/backend/scripts/docker-entrypoint.sh
COPY --chown=septum:septum backend/alembic.ini /app/backend/alembic.ini
COPY --chown=septum:septum backend/alembic/ /app/backend/alembic/

# Copy frontend (standalone build)
COPY --from=frontend-builder --chown=septum:septum /app/.next/standalone /app/frontend/
COPY --from=frontend-builder --chown=septum:septum /app/.next/static /app/frontend/.next/static
COPY --from=frontend-builder --chown=septum:septum /app/public /app/frontend/public

# Writable dirs
RUN mkdir -p /app/data /app/uploads /app/anon_maps \
    && chown -R septum:septum /app/data /app/uploads /app/anon_maps

# Startup script
COPY --chown=septum:septum <<'STARTUP' /app/start.sh
#!/bin/bash
set -e

# Start backend
cd /app/backend
export PATH="/app/backend/.venv/bin:$PATH"
bash ./scripts/docker-entrypoint.sh &
BACKEND_PID=$!

# Wait for backend health
echo "Waiting for backend..."
for i in $(seq 1 60); do
  if python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" 2>/dev/null; then
    echo "Backend ready."
    break
  fi
  sleep 2
done

# Start frontend
cd /app/frontend
node server.js &
FRONTEND_PID=$!

echo "Septum is running — backend :8000, frontend :3000"

# Wait for either process to exit
wait -n $BACKEND_PID $FRONTEND_PID
exit $?
STARTUP
RUN chmod +x /app/start.sh

USER septum

EXPOSE 8000 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" || exit 1

CMD ["/app/start.sh"]
