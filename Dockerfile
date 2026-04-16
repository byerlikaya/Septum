# -----------------------------------------------------------------------------
# Septum — combined backend + frontend single-container image (published).
#
# One process tree: api (uvicorn on :8000) + web (Next.js on :3000). SQLite
# default; setup wizard configures everything on first run — no .env file
# needed.
#
# This is the image published as ``byerlikaya/septum:latest``. The modular
# compose variants (docker-compose.airgap.yml / .gateway.yml) ship separate
# per-module images built from docker/api.Dockerfile etc; pick one or the
# other, not both.
#
# Build:
#   docker build -f docker/standalone.Dockerfile -t septum/standalone .
#
# Run:
#   docker run -p 3000:3000 septum/standalone
# -----------------------------------------------------------------------------

# ── Stage 1: build Python backend dependencies ──
FROM python:3.12-slim AS backend-builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libmagic1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN python -m venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

COPY packages/api/requirements.txt /tmp/requirements.txt
# Install CPU-only PyTorch first (saves ~6 GB by excluding CUDA/nvidia/triton).
# pip will skip torch when processing requirements.txt because 2.10.0+cpu
# satisfies the torch==2.10.0 pin (PEP 440 ignores local version tags).
RUN pip install --no-warn-script-location \
    torch==2.10.0 --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-warn-script-location -r /tmp/requirements.txt

# Install septum-core + septum-queue + septum-api under /app so the
# builder + runtime stages agree on the editable-install source path.
COPY packages/core/ /app/packages/core/
COPY packages/queue/ /app/packages/queue/
COPY packages/api/ /app/packages/api/
RUN pip install --no-warn-script-location -e /app/packages/core \
    && pip install --no-warn-script-location -e /app/packages/queue \
    && pip install --no-warn-script-location -e /app/packages/api

# ── Stage 2: build Next.js frontend ──
FROM node:20-alpine AS frontend-builder

WORKDIR /app
COPY packages/web/package.json packages/web/package-lock.json* ./
RUN npm ci || npm install

COPY packages/web/ .
COPY VERSION /tmp/VERSION
RUN mkdir -p public
RUN echo "NEXT_PUBLIC_APP_VERSION=$(cat /tmp/VERSION | tr -d '[:space:]')" >> .env.local && npm run build

# ── Stage 3: combined runtime ──
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    NODE_ENV=production \
    DOCKER=true \
    DB_PATH=/app/data/septum.db \
    DOCUMENT_STORAGE_DIR=/app/uploads \
    ANON_MAP_STORAGE_DIR=/app/anon_maps \
    VECTOR_INDEX_DIR=/app/vector_indexes \
    BM25_INDEX_DIR=/app/bm25_indexes

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

# Copy version file
COPY --chown=septum:septum VERSION /app/VERSION

# Copy backend venv + packages (editable install resolves through /app/packages)
COPY --from=backend-builder /app/.venv /app/.venv
COPY --from=backend-builder /app/packages /app/packages

# Copy frontend (standalone build)
COPY --from=frontend-builder --chown=septum:septum /app/.next/standalone /app/frontend/
COPY --from=frontend-builder --chown=septum:septum /app/.next/static /app/frontend/.next/static
COPY --from=frontend-builder --chown=septum:septum /app/public /app/frontend/public

# Writable dirs — declared as VOLUME so data persists across container recreations
RUN mkdir -p /app/data /app/uploads /app/anon_maps /app/vector_indexes /app/bm25_indexes /app/models \
    && chown -R septum:septum /app/data /app/uploads /app/anon_maps /app/vector_indexes /app/bm25_indexes /app/models /app/packages

VOLUME ["/app/data", "/app/uploads", "/app/anon_maps", "/app/vector_indexes", "/app/bm25_indexes", "/app/models"]

# Startup script
COPY --chown=septum:septum <<'STARTUP' /app/start.sh
#!/bin/bash
set -e

# Symlink ML model caches to the persistent /app/models volume so
# Whisper, HuggingFace, and PaddleOCR models survive container recreation.
mkdir -p /app/models/whisper /app/models/huggingface /app/models/paddlex \
         /home/septum/.cache
ln -sfn /app/models/whisper /home/septum/.cache/whisper
ln -sfn /app/models/huggingface /home/septum/.cache/huggingface
ln -sfn /app/models/paddlex /home/septum/.paddlex

# Start backend
cd /app/packages/api
export PATH="/app/.venv/bin:$PATH"
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

# Start frontend (HOSTNAME=0.0.0.0 required for Docker — Next.js 14+
# defaults to localhost which is unreachable from outside the container)
cd /app/frontend
HOSTNAME=0.0.0.0 PORT=3000 node server.js &
FRONTEND_PID=$!

echo "Septum is running on http://localhost:3000"

# Wait for either process to exit
wait -n $BACKEND_PID $FRONTEND_PID
exit $?
STARTUP
RUN chmod +x /app/start.sh

USER septum

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3000/health')" || exit 1

CMD ["/app/start.sh"]
