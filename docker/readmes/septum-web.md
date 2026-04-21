<p align="center">
  <img src="https://raw.githubusercontent.com/byerlikaya/Septum/main/assets/septum_logo.png" alt="Septum logo" width="180" />
</p>

<h3 align="center">Septum Web — Next.js 16 dashboard (air-gapped zone)</h3>

<p align="center">
  <img src="https://img.shields.io/badge/zone-air--gapped-1E88E5" alt="Air-gapped zone" />
  <a href="https://github.com/byerlikaya/Septum"><img src="https://img.shields.io/badge/GitHub-Source-181717?logo=github" alt="GitHub" /></a>
  <a href="https://github.com/byerlikaya/Septum/blob/main/docs/SCREENSHOTS.md"><img src="https://img.shields.io/badge/docs-Screenshots-blue" alt="Screenshots" /></a>
  <a href="https://github.com/byerlikaya/Septum/blob/main/CHANGELOG.md"><img src="https://img.shields.io/badge/docs-Changelog-blue" alt="Changelog" /></a>
</p>

---

Next.js 16 (App Router + React 19) dashboard for Septum: setup wizard, document preview with entity highlights, approval gate, settings, custom regulation rules, audit trail.

Runs alongside [`septum-api`](https://hub.docker.com/r/byerlikaya/septum-api). For the all-in-one image see [`septum`](https://hub.docker.com/r/byerlikaya/septum).

## Same-origin vs split deployment

The image takes a **build-arg** `NEXT_PUBLIC_API_BASE_URL`:

- **Unset (default)** — relative `/api/*` URLs, proxied via Next.js rewrites to the API container. Use this when the dashboard and API share an origin.
- **Set to the API URL** (e.g. `https://api.example.com`) — dashboard talks directly to the API at that host. Use this for split deployments.

```bash
# Same-origin (docker compose with Next.js rewrites)
docker run --name septum-web -p 3000:3000 byerlikaya/septum-web

# Split deployment
docker build --build-arg NEXT_PUBLIC_API_BASE_URL=https://api.example.com ...
```

## docker-compose

```bash
docker compose -f docker-compose.airgap.yml up
```

Brings up `septum-web` + `septum-api` + PostgreSQL + Redis. Dashboard served on `:3000`, API proxied at `/api/*`.

## Tags

| Tag | Contents |
|---|---|
| `latest`, `1`, `1.0`, `1.0.0` | Multi-arch (amd64 + arm64), production Next.js build |

## Links

- **Source:** [github.com/byerlikaya/Septum](https://github.com/byerlikaya/Septum)
- **Screenshots:** [docs/SCREENSHOTS.md](https://github.com/byerlikaya/Septum/blob/main/docs/SCREENSHOTS.md)
- **Architecture:** [docs/ARCHITECTURE.md](https://github.com/byerlikaya/Septum/blob/main/docs/ARCHITECTURE.md)
