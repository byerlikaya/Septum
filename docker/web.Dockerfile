# -----------------------------------------------------------------------------
# septum-web — Next.js 16 dashboard (air-gapped zone)
#
# Runs the frontend standalone. Point it at a separate septum-api via
# NEXT_PUBLIC_API_BASE_URL at build time (baked into the static bundle).
# If unset the frontend falls back to same-origin relative URLs, suitable
# for the single-container / reverse-proxy topology.
#
# Build:
#   docker build \
#     --build-arg NEXT_PUBLIC_API_BASE_URL=https://api.example.com \
#     -f docker/web.Dockerfile -t septum/web .
#
# Run:
#   docker run -p 3000:3000 septum/web
# -----------------------------------------------------------------------------

# ── build ──
FROM node:20-alpine AS builder

ARG NEXT_PUBLIC_API_BASE_URL=""

WORKDIR /app
COPY packages/web/package.json packages/web/package-lock.json* ./
RUN npm ci || npm install

COPY packages/web/ .
COPY VERSION /tmp/VERSION
RUN mkdir -p public \
    && printf "NEXT_PUBLIC_APP_VERSION=%s\nNEXT_PUBLIC_API_BASE_URL=%s\n" \
        "$(cat /tmp/VERSION | tr -d '[:space:]')" \
        "${NEXT_PUBLIC_API_BASE_URL}" \
        > .env.local \
    && npm run build

# ── runtime ──
FROM node:20-alpine AS runtime

ENV NODE_ENV=production \
    HOSTNAME=0.0.0.0 \
    PORT=3000

WORKDIR /app

COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

RUN addgroup --gid 1000 septum \
    && adduser --uid 1000 --ingroup septum --disabled-password septum \
    && chown -R septum:septum /app

USER septum

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD wget -qO- http://127.0.0.1:3000 >/dev/null || exit 1

CMD ["node", "server.js"]
