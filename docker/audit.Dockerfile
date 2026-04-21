# -----------------------------------------------------------------------------
# septum-audit — compliance audit trail (internet-facing zone)
#
# Lightweight image — no torch, no Presidio. Persists already-masked
# event records into a JSONL sink and serves /api/audit/export in
# JSON / CSV / Splunk HEC.
#
# CRITICAL: this image MUST NOT contain septum-core. The dependency wall
# is what keeps raw PII out of the audit zone.
#
# Build:
#   docker build -f docker/audit.Dockerfile -t septum/audit .
#
# Run (with Redis queue + jsonl sink):
#   docker run -e SEPTUM_QUEUE_URL=redis://redis:6379/0 \
#              -e SEPTUM_AUDIT_SINK_PATH=/var/log/septum/audit.jsonl \
#              -v septum-audit-log:/var/log/septum \
#              septum/audit
# -----------------------------------------------------------------------------

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
RUN python -m venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

COPY packages/queue/ /app/packages/queue/
COPY packages/audit/ /app/packages/audit/

# Editable install records the source path, so builder + runtime stages
# must agree on the package location (both use /app/packages).
RUN pip install --no-warn-script-location -e "/app/packages/queue[redis]" \
    && pip install --no-warn-script-location -e "/app/packages/audit[queue,server]"

# ── runtime ──
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    SEPTUM_AUDIT_SINK_PATH=/var/log/septum/audit.jsonl

RUN groupadd --gid 1000 septum \
    && useradd --uid 1000 --gid septum --shell /bin/sh --create-home septum

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/packages /app/packages

RUN mkdir -p /var/log/septum && chown -R septum:septum /var/log/septum

VOLUME ["/var/log/septum"]

USER septum

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8002/health')" || exit 1

CMD ["python", "-m", "septum_audit"]
