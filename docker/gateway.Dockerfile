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

WORKDIR /app
RUN python -m venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

COPY packages/queue/ /app/packages/queue/
COPY packages/gateway/ /app/packages/gateway/

# [redis] extra pulls redis>=5.2.0 for RedisStreamsQueueBackend;
# [server] extra pulls fastapi+uvicorn so /health is reachable.
# Editable install records the source path, so builder + runtime stages
# must agree on the package location (both use /app/packages).
RUN pip install --no-warn-script-location -e "/app/packages/queue[redis]" \
    && pip install --no-warn-script-location -e "/app/packages/gateway[server]"

# ── runtime ──
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --gid 1000 septum \
    && useradd --uid 1000 --gid septum --shell /bin/sh --create-home septum

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/packages /app/packages

USER septum

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health')" || exit 1

CMD ["python", "-m", "septum_gateway"]
