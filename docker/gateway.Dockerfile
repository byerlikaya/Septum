# -----------------------------------------------------------------------------
# septum-gateway — cloud LLM forwarder (internet-facing zone)
#
# Lightweight image — no torch, no Presidio, no spaCy. Pulls in only
# septum-queue + septum-gateway + httpx + redis. Running image is ~100 MB
# compared to ~4 GB for the api image that ships the full ML stack.
#
# CRITICAL: this image MUST NOT contain septum-core. The dependency wall
# is what keeps raw PII out of the internet-facing zone — the worker only
# ever sees payloads the air-gapped api has already sanitized.
#
# Build:
#   docker build -f docker/gateway.Dockerfile -t septum/gateway .
#
# Run (with Redis queue):
#   docker run -e SEPTUM_QUEUE_URL=redis://redis:6379/0 \
#              -e ANTHROPIC_API_KEY=sk-... \
#              septum/gateway
# -----------------------------------------------------------------------------

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
RUN python -m venv /build/.venv
ENV PATH="/build/.venv/bin:$PATH"

COPY packages/queue/ /build/packages/queue/
COPY packages/gateway/ /build/packages/gateway/

# [redis] extra pulls redis>=5.2.0 for RedisStreamsQueueBackend;
# [server] extra pulls fastapi+uvicorn so /health is reachable.
RUN pip install --no-warn-script-location -e "/build/packages/queue[redis]" \
    && pip install --no-warn-script-location -e "/build/packages/gateway[server]"

# ── runtime ──
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 septum \
    && useradd --uid 1000 --gid septum --shell /bin/sh --create-home septum

WORKDIR /app
COPY --from=builder /build/.venv /app/.venv
COPY --from=builder /build/packages /app/packages

USER septum

EXPOSE 8001

# /health served by the FastAPI app (run it as a sidecar alongside the
# worker — compose variants wire both services pointing at the same image).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8001/health >/dev/null || exit 1

# Default: run the worker. Override CMD with uvicorn to serve /health
# instead, or run two containers from the same image.
CMD ["python", "-m", "septum_gateway"]
