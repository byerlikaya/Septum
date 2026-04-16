# -----------------------------------------------------------------------------
# septum-web — Next.js 16 dashboard (air-gapped zone)
#
# Runs the frontend standalone. Point it at a separate septum-api via
# NEXT_PUBLIC_API_BASE_URL at build time (baked into the static bundle).
# If unset the frontend falls back to same-origin relative URLs, suitable
# for the single-container / reverse-proxy topology.
#
# BACKEND_INTERNAL_URL is the server-side proxy target used by Next.js
# rewrites for /api/* / /health / /docs / etc. Next.js bakes the rewrite
# destination into its routes manifest at build time, so this must be
# passed as a build-arg (setting it at runtime has no effect).
#
# Build:
#   docker build \
#     --build-arg NEXT_PUBLIC_API_BASE_URL=https://api.example.com \
#     --build-arg BACKEND_INTERNAL_URL=http://api:8000 \
#     -f docker/web.Dockerfile -t septum/web .
#
# Run:
#   docker run -p 3000:3000 septum/web
# -----------------------------------------------------------------------------

# ── build ──
FROM node:20-alpine AS builder

ARG NEXT_PUBLIC_API_BASE_URL=""
# Matches the runtime fallback in next.config.mjs for local dev without a
# compose network; docker-compose.yml overrides it with http://api:8000.
ARG BACKEND_INTERNAL_URL=http://127.0.0.1:8000
# Version is injected by the Docker Hub publish workflow from the git
# tag; defaults to 0.0.0-dev for local builds that skip --build-arg.
ARG VERSION=0.0.0-dev

WORKDIR /app
COPY packages/web/package.json packages/web/package-lock.json* ./
RUN npm ci || npm install

COPY packages/web/ .
RUN mkdir -p public \
    && printf "NEXT_PUBLIC_APP_VERSION=%s\nNEXT_PUBLIC_API_BASE_URL=%s\nBACKEND_INTERNAL_URL=%s\n" \
        "${VERSION}" \
        "${NEXT_PUBLIC_API_BASE_URL}" \
        "${BACKEND_INTERNAL_URL}" \
        > .env.local \
    && npm run build

# ── runtime ──
FROM node:20-alpine AS runtime

ENV NODE_ENV=production \
    HOSTNAME=0.0.0.0 \
    PORT=3000

WORKDIR /app

COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
COPY --from=builder --chown=node:node /app/public ./public

# node:20-alpine ships a uid=1000 'node' user by default — reuse it instead
# of creating a second one (the python images create 'septum' because they
# do NOT have a pre-existing non-root user).
USER node

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD wget -qO- http://127.0.0.1:3000 >/dev/null || exit 1

CMD ["node", "server.js"]
