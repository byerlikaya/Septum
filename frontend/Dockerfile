# -----------------------------------------------------------------------------
# Septum Frontend — multi-stage Dockerfile (Next.js standalone)
# -----------------------------------------------------------------------------

# Stage 1: dependencies and build
FROM node:20-alpine AS builder

WORKDIR /app

COPY package.json ./
RUN npm install

COPY . .
RUN mkdir -p public
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL:-http://localhost:8000}
RUN npm run build

# Stage 2: runtime
FROM node:20-alpine AS runtime

ENV NODE_ENV=production \
    PORT=3000

RUN addgroup -g 1001 septum \
    && adduser -u 1001 -G septum -s /bin/sh -D septum

WORKDIR /app

COPY --from=builder --chown=1001:1001 /app/.next/standalone ./
COPY --from=builder --chown=1001:1001 /app/.next/static ./.next/static
COPY --from=builder --chown=1001:1001 /app/public ./public

USER septum

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD node -e "require('http').get('http://127.0.0.1:3000/', (r) => process.exit(r.statusCode === 200 ? 0 : 1)).on('error', () => process.exit(1))"

CMD ["node", "server.js"]
