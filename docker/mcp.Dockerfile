# -----------------------------------------------------------------------------
# septum-mcp — Model Context Protocol server (air-gapped zone)
#
# Built as a Docker image for remote / container deployments. The image
# defaults to streamable-http transport on port 8765 — that's the mode
# that makes sense inside a container orchestrator (stdio clients spawn
# the server as a subprocess on their own host, no container needed).
#
# Stdio mode is still available for enterprises that run the MCP client
# inside a container:
#
#   docker run -i --rm -e SEPTUM_MCP_TRANSPORT=stdio septum/mcp
#
# Default (HTTP mode, with bearer token):
#
#   docker run -p 8765:8765 \
#     -e SEPTUM_MCP_HTTP_TOKEN=<random-secret> \
#     -e SEPTUM_MCP_HTTP_HOST=0.0.0.0 \
#     septum/mcp
#
# The image includes septum-core so the MCP tools (mask_text,
# unmask_response, detect_pii, scan_file, list_regulations) work
# without a network round-trip.
# -----------------------------------------------------------------------------

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN python -m venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# MCP runs as a stdio subprocess with short per-call latency where a CUDA
# context start-up is pure overhead — pin torch to the CPU wheel so the
# image does not drag in ~5 GB of CUDA shared libs no one will use.
# Users who want GPU-accelerated NER should run septum-api (which ships
# a dedicated -gpu variant) instead.
COPY docker/scripts/install-torch.sh /tmp/install-torch.sh
RUN sh /tmp/install-torch.sh cpu

COPY packages/core/ /app/packages/core/
COPY packages/mcp/ /app/packages/mcp/

# Editable install records the source path, so builder + runtime stages
# must agree on the package location (both use /app/packages).
RUN pip install --no-warn-script-location -e "/app/packages/core[transformers]" \
    && pip install --no-warn-script-location -e /app/packages/mcp

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

ENV SEPTUM_MCP_TRANSPORT=streamable-http \
    SEPTUM_MCP_HTTP_HOST=0.0.0.0 \
    SEPTUM_MCP_HTTP_PORT=8765

EXPOSE 8765

# /health is answered by septum_mcp.auth.BearerTokenMiddleware and
# always bypasses bearer auth, so this probe works regardless of
# whether SEPTUM_MCP_HTTP_TOKEN is set. curl is already installed in
# the python:3.12-slim base.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health').read()" || exit 1

CMD ["python", "-m", "septum_mcp.server"]
