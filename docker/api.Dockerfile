# -----------------------------------------------------------------------------
# septum-api — FastAPI REST layer (air-gapped zone)
#
# Runs the Python backend only. The Next.js dashboard is shipped separately
# (docker/web.Dockerfile); a split deployment wires this image behind the
# same ingress as web via NEXT_PUBLIC_API_BASE_URL.
#
# Build:
#   docker build -f docker/api.Dockerfile -t septum/api .
#
# Run:
#   docker run -p 8000:8000 -v septum-data:/app/data septum/api
# -----------------------------------------------------------------------------

FROM python:3.12-slim AS builder

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
# CPU-only torch first (saves ~6 GB by excluding CUDA/nvidia/triton).
# pip will skip torch when processing requirements.txt because 2.10.0+cpu
# satisfies the torch==2.10.0 pin (PEP 440 ignores local version tags).
RUN pip install --no-warn-script-location \
    torch==2.10.0 --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-warn-script-location -r requirements.txt

# Install septum-core + septum-queue so the api can use them directly.
COPY packages/core/ /build/packages/core/
COPY packages/queue/ /build/packages/queue/
COPY packages/api/ /build/packages/api/
RUN pip install --no-warn-script-location -e /build/packages/core \
    && pip install --no-warn-script-location -e /build/packages/queue \
    && pip install --no-warn-script-location -e /build/packages/api

# ── runtime ──
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    DOCKER=true \
    DB_PATH=/app/data/septum.db \
    DOCUMENT_STORAGE_DIR=/app/uploads \
    ANON_MAP_STORAGE_DIR=/app/anon_maps \
    VECTOR_INDEX_DIR=/app/vector_indexes \
    BM25_INDEX_DIR=/app/bm25_indexes

RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagic1 ffmpeg curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 septum \
    && useradd --uid 1000 --gid septum --shell /bin/sh --create-home septum

WORKDIR /app

COPY --chown=septum:septum VERSION /app/VERSION
COPY --from=builder /build/.venv /app/.venv
COPY --from=builder /build/packages /app/packages
COPY --chown=septum:septum backend/app/ /app/backend/app/
COPY --chown=septum:septum backend/scripts/docker-entrypoint.sh /app/backend/scripts/docker-entrypoint.sh
COPY --chown=septum:septum backend/alembic.ini /app/backend/alembic.ini
COPY --chown=septum:septum backend/alembic/ /app/backend/alembic/

RUN mkdir -p /app/data /app/uploads /app/anon_maps /app/vector_indexes /app/bm25_indexes /app/models \
    && chown -R septum:septum /app /app/data /app/uploads /app/anon_maps /app/vector_indexes /app/bm25_indexes /app/models

VOLUME ["/app/data", "/app/uploads", "/app/anon_maps", "/app/vector_indexes", "/app/bm25_indexes", "/app/models"]

USER septum
WORKDIR /app/backend

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" || exit 1

CMD ["bash", "./scripts/docker-entrypoint.sh"]
