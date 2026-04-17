# septum-web

Air-gapped Next.js dashboard for [Septum](https://github.com/byerlikaya/Septum). Ships the document management UI, approval gate for masked prompts, chat view, regulation settings, and setup wizard — the human-facing half of the privacy-first middleware.

All PII detection and masking runs behind the dashboard in `septum-api` + `septum-core`. This package is transport-only: it calls the REST API and streams chat responses over SSE. It never sees raw PII on its own.

## Stack

- **Next.js 16** (App Router) + **React 19** + **TypeScript 5**
- **Tailwind CSS 3** for styling
- **Axios** for REST, **fetch + ReadableStream** for SSE chat
- **Jest + React Testing Library** for unit tests

## Install

```bash
cd packages/web
npm install
```

## Development

```bash
npm run dev        # webpack, 4 GB heap, port 3000
npm run build      # production build
npm run start      # serve the production build
npm run lint       # ESLint
npm test           # Jest
```

From the repo root `./dev.sh` starts this together with the backend.

## Deployment layouts

The dashboard supports two topologies controlled by two environment variables:

### 1. Single-container (default)

Frontend and backend share the same origin. Next.js rewrites in `next.config.mjs` proxy `/api/*`, `/health`, `/metrics`, `/docs`, `/redoc`, and `/openapi.json` to the backend process.

```bash
# No build-time URL needed — requests stay relative.
npm run build && npm run start
```

- `NEXT_PUBLIC_API_BASE_URL` — **unset**. `baseURL` resolves to `""` at module load, so Axios and `fetch()` call relative paths.
- `BACKEND_INTERNAL_URL` — baked at build time into the Next.js routes manifest. For local dev (`npm run dev`) it is read from the environment at startup; for Docker builds it must be passed as a build-arg (see `docker/web.Dockerfile`). Default: `http://127.0.0.1:8000`.

### 2. Split deployment

Dashboard and API run on different origins (e.g. `app.septum.example` ↔ `api.septum.example`).

```bash
NEXT_PUBLIC_API_BASE_URL=https://api.septum.example npm run build
npm run start
```

- `NEXT_PUBLIC_API_BASE_URL` — inlined into the browser bundle at build time. Trailing slashes are stripped so callers can keep concatenating `${baseURL}/api/...` cleanly (`resolveBaseURL` in `src/lib/api.ts`).
- The backend must be started with `FRONTEND_ORIGIN=https://app.septum.example` so its CORS allow-list permits the dashboard origin. Comma-separated values are supported for multi-origin setups (`FRONTEND_ORIGIN=https://app.example,https://admin.example`); `*` keeps the permissive wildcard.

## Structure

```
src/
├── app/          # Next.js App Router pages (chat, documents, chunks, settings, setup wizard)
├── components/   # Stateless UI, organized by feature
├── hooks/        # Shared React hooks
├── i18n/         # Translations (English default + Turkish)
├── lib/          # API client (axios), types, utilities
└── store/        # Shared state hooks (chat, documents, settings, regulations)
```

All REST calls go through `src/lib/api.ts` — no direct `fetch`/`axios` in components. Shared TypeScript interfaces live in `src/lib/types.ts`.

## License

MIT
