# -----------------------------------------------------------------------------
# Septum Backend — multi-stage Dockerfile (non-root user, EXPOSE 8000)
# -----------------------------------------------------------------------------

# Stage 1: build dependencies and virtualenv
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libmagic1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

RUN python -m venv /build/.venv
ENV PATH="/build/.venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-warn-script-location -r requirements.txt

# Stage 2: runtime image
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libmagic1 \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 septum \
    && useradd --uid 1000 --gid septum --shell /bin/sh --create-home septum

WORKDIR /app

# Copy virtualenv from builder
COPY --from=builder /build/.venv /app/.venv

# Copy application and entrypoint
COPY --chown=septum:septum app/ ./app/
COPY --chown=septum:septum scripts/docker-entrypoint.sh ./scripts/docker-entrypoint.sh
COPY --chown=septum:septum alembic.ini ./alembic.ini
COPY --chown=septum:septum alembic/ ./alembic/

# Writable dirs for DB, uploads, and anon maps (override via env if needed)
ENV DB_PATH=/app/data/septum.db
RUN mkdir -p /app/data /app/uploads /app/anon_maps \
    && chown -R septum:septum /app/data /app/uploads /app/anon_maps

USER septum

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" || exit 1

ENTRYPOINT ["bash", "./scripts/docker-entrypoint.sh"]
