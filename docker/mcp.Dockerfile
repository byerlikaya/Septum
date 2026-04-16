# -----------------------------------------------------------------------------
# septum-mcp — Model Context Protocol server (air-gapped zone)
#
# Stdio-based MCP server for Claude Code / Desktop / Cursor. Built as a
# Docker image for rare enterprise deployments where the MCP client runs
# inside a container orchestrator; most users invoke it directly via
# `uvx septum-mcp`.
#
# The image includes septum-core so the MCP tools (mask_text,
# unmask_response, detect_pii, scan_file, list_regulations) work without
# a network round-trip.
#
# Run (stdio-attached from an orchestrator):
#   docker run -i --rm septum/mcp
# -----------------------------------------------------------------------------

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN python -m venv /build/.venv
ENV PATH="/build/.venv/bin:$PATH"

COPY packages/core/ /build/packages/core/
COPY packages/mcp/ /build/packages/mcp/

RUN pip install --no-warn-script-location -e "/build/packages/core[transformers]" \
    && pip install --no-warn-script-location -e /build/packages/mcp

# ── runtime ──
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --gid 1000 septum \
    && useradd --uid 1000 --gid septum --shell /bin/sh --create-home septum

WORKDIR /app
COPY --from=builder /build/.venv /app/.venv
COPY --from=builder /build/packages /app/packages

USER septum

# No EXPOSE — MCP uses stdio. HEALTHCHECK would require an HTTP surface
# the package does not provide; the orchestrator should treat the
# subprocess exit code as the liveness signal instead.

CMD ["python", "-m", "septum_mcp"]
