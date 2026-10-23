# Septum — Installation Guide

<p align="center">
  <a href="./index.md"><strong>🏠 Home</strong></a>
  &nbsp;·&nbsp;
  <strong>🚀 Installation</strong>
  &nbsp;·&nbsp;
  <a href="benchmark.md"><strong>📈 Benchmark</strong></a>
  &nbsp;·&nbsp;
  <a href="features.md"><strong>✨ Features</strong></a>
  &nbsp;·&nbsp;
  <a href="architecture.md"><strong>🏗️ Architecture</strong></a>
  &nbsp;·&nbsp;
  <a href="document-ingestion.md"><strong>📊 Document Ingestion</strong></a>
  &nbsp;·&nbsp;
  <a href="screenshots.md"><strong>📸 Screenshots</strong></a>
</p>

---

## Table of Contents

- [⚡ Quickstart](#-quickstart)
- [System requirements](#system-requirements)
- [Installation variants](#installation-variants)
  - [Full local stack (recommended)](#1-full-local-stack--recommended)
  - [Standalone single container (demo)](#2-standalone-single-container--demo)
  - [Air-gapped zone only](#3-air-gapped-zone-only)
  - [Internet-facing zone only](#4-internet-facing-zone-only)
  - [Native / source install](#5-native--source-install-contributors)
- [First launch — setup wizard](#first-launch--setup-wizard)
- [Access and network settings](#access-and-network-settings)
- [LLM providers](#llm-providers)
- [Data persistence & volumes](#data-persistence--volumes)
- [Upgrading](#upgrading)
- [Troubleshooting](#troubleshooting)
- [Uninstalling](#uninstalling)

---

## ⚡ Quickstart

The shortest path to a working Septum. Three commands, five minutes, a local-first AI middleware with Ollama bundled and ready.

```bash
git clone https://github.com/byerlikaya/Septum.git && cd Septum
cp .env.example .env
# Open .env in your editor and set POSTGRES_PASSWORD, REDIS_PASSWORD and the four SEPTUM_QUEUE_*_PASSWORD values
docker compose up --build
```

Compose stops with an error naming any variable that is still empty.

Open **http://localhost:3000**. The setup wizard's first step asks for a setup token, which the API prints to its log: `docker compose logs api`. The wizard takes you the rest of the way. See [Setup token](#setup-token).

The compose stack brings up PostgreSQL, Redis, Ollama, the FastAPI backend, and the Next.js dashboard on a single host. Septum is installed from source: the first run builds the Septum images locally (several minutes) and pulls the base services and a default Ollama model; subsequent runs reuse the build cache. No raw PII ever leaves the machine — detection, masking, and LLM inference all happen locally.

---

## System requirements

| Resource | Minimum | Recommended |
|---|---|---|
| CPU | 2 cores (x86-64 or arm64) | 4+ cores |
| RAM | 6 GB free | 16 GB (so Ollama can hold a 7B-class model) |
| Disk | 12 GB free | 30 GB (models + document indexes grow) |
| Docker | Desktop 4.30+ / Engine 24+ with Compose v2 | Latest stable |
| OS | macOS 13+, Windows 10/11 with WSL2, any mainstream Linux | — |

**Platform notes.**

- **Apple Silicon (M1/M2/M3/M4)** — fully supported. Ollama uses Metal acceleration automatically; no extra flags needed. The images build natively for arm64.
- **NVIDIA GPU (Linux amd64)** — optional. Build the `docker/standalone.Dockerfile` or `docker/api.Dockerfile` image with the build argument `TORCH_VARIANT=gpu` (add `TORCH_VARIANT: gpu` under the service's `build.args` in the compose file and run `up --build`; or build it yourself with `docker build -f docker/api.Dockerfile --build-arg TORCH_VARIANT=gpu -t septum-api:local .` and start the stack without `--build` so compose keeps that tag) for CUDA-accelerated PyTorch (faster OCR, Whisper, embedding). The host needs an NVIDIA driver with CUDA 13 support. Ollama runs on the GPU automatically when one is visible inside the container.
- **Windows** — WSL2 is required; native Windows containers are not supported. All paths and volume examples assume a POSIX shell; run them from a WSL terminal.

---

## Installation variants

Five supported topologies. Pick the one that matches your deployment shape.

### 1. Full local stack — **recommended**

Single host, all modules plus Ollama, PostgreSQL, Redis. The default `docker-compose.yml` ships this topology. It is a development stack: every service shares one network with internet access, so zone isolation is not enforced. For a split between zones, use the air-gapped and internet-facing files in variants 3 and 4. In this stack one Redis holds both the queue and the rate-limit and setup-token keys, under `noeviction`. A stream backlog (for example, with the audit worker down) can fill it; after that, login attempts are refused. The split topologies keep these in separate Redis instances. Size the queue with `SEPTUM_QUEUE_REDIS_MAXMEMORY`. This Redis runs without persistence and has no data volume, so the rate-limit and setup-token attempt counters reset when it restarts.

```bash
git clone https://github.com/byerlikaya/Septum.git && cd Septum
cp .env.example .env
# Edit .env: set POSTGRES_PASSWORD, REDIS_PASSWORD and the four SEPTUM_QUEUE_*_PASSWORD values (required)

docker compose up --build       # foreground — watch the first-boot logs
# or
docker compose up -d --build    # detached — release the terminal

# First-time: pull a default model into the bundled Ollama
docker compose exec ollama ollama pull llama3.2:3b
```

Navigate to **http://localhost:3000**. See the [First launch](#first-launch--setup-wizard) section for the wizard walkthrough.

The optional `mcp` service (profile `mcp`) mounts the models volume read-only, so it only loads models that are already in the volume and pass the hash check. Warm `api` or run the fetch command first (`docker compose run --rm api python -m septum_api.cli.models fetch`) before relying on `mcp` for languages that `api` has not loaded yet.

To run without Ollama (cloud-provider-only deployments):

```bash
docker compose -f docker-compose.yml -f docker-compose.no-ollama.yml up --build
```

### 2. Standalone single container — **demo**

One container, SQLite, no external services. Fastest way to try Septum on a single laptop; ideal for sales demos and hobby deployments.

```bash
git clone https://github.com/byerlikaya/Septum.git && cd Septum
docker compose -f docker-compose.standalone.yml up -d --build
```

The setup token is printed to the container log: `docker compose -f docker-compose.standalone.yml logs septum`.

Standalone does not bundle Ollama. Either run Ollama on the host (`brew install ollama && ollama serve`) and point the wizard at `http://host.docker.internal:11434`, or skip Ollama and use a cloud provider (Anthropic / OpenAI / OpenRouter).

**Use standalone when**: you want the fastest try-it-out experience, your corpus is small, and you don't need the full modular zone separation.

**Don't use standalone when**: you need air-gap separation, multi-user scale, or the full Septum feature set (semantic PII layer + Auto-RAG routing both want Ollama).

### 3. Air-gapped zone only

Runs only the air-gapped zone: api, web, PostgreSQL, Redis, Ollama, and mcp when its profile is enabled. All of them sit on the `zone` network, which is internal. On Docker Desktop it has no route to the internet, and a port published on it is not reachable from the host (see the notes below). The only container on both `zone` and the normal bridge is `zone-edge`, a single haproxy relay, so it is the only way in or out:

- `:3000` from the host to the dashboard (web).
- `:8765` from the host to mcp, when the `mcp` profile is enabled (bound to `SEPTUM_MCP_BIND`).
- `:6379` from the api to the gateway-zone Redis at `SEPTUM_GATEWAY_QUEUE_ADDR`.
Ollama runs inside the zone as the `ollama` service, reached as `http://ollama:11434`. It has no published port and no route out, so model pulls from the dashboard and `*-cloud` model tags fail there. Models are loaded with the one-off `ollama-pull` service, which is not on `zone`:

```bash
docker compose -f docker-compose.airgap.yml run --rm ollama-pull llama3.2:3b
```

The one-off starts a throwaway Ollama on `edge`, pulls the named models into the shared `ollama-data` volume and exits. On a host with no internet access, copy the models from a connected host into that volume instead. `<project>` is the Compose project name (`septum` for a checkout in a directory named `Septum`):

```bash
docker run --rm -v <project>_ollama-data:/dst -v "$HOME/.ollama":/src:ro alpine cp -a /src/models /dst/
```

Containers on `zone` have no route out. This was verified on Docker Desktop. The `zone` network is `internal`, and `zone-edge` is the only container that also sits on the normal bridge. Its relay carries only the paths above, and the queue path accepts connections only from the api's fixed address (`SEPTUM_API_IP`). The relay logs no traffic and no client addresses. It writes only startup and configuration notices, which go to the Docker log driver (`docker compose -f docker-compose.airgap.yml logs zone-edge`).

Native Linux Docker Engine has not been tested. An internal network still has a gateway address on the host, so a host service bound to `0.0.0.0` may be reachable from zone containers. Bind host services to loopback or to one specific interface.

```bash
cp .env.example .env
# Set POSTGRES_PASSWORD, REDIS_PASSWORD and SEPTUM_QUEUE_API_PASSWORD (the same value as on the gateway host)
# Set SEPTUM_GATEWAY_QUEUE_ADDR to <gateway-private-ip>:6379

docker compose -f docker-compose.airgap.yml up -d --build
```

Compose refuses to start without `SEPTUM_GATEWAY_QUEUE_ADDR` and `SEPTUM_QUEUE_API_PASSWORD`. The file sets `USE_GATEWAY_DEFAULT=true`, which does two things. The api refuses to start without a queue. And it forces gateway routing: while the variable is true, every cloud LLM call goes through the queue, whatever the stored `use_gateway` setting says. The stored setting is not changed by the variable. Each cloud LLM call is published as a masked request to the gateway host's Redis and answered from there, and the api connects as the `septum-api` ACL user, which reaches only the request and response streams. On Docker Desktop, the internal `zone` network gives a direct cloud call no route out, so the queue is the only path (see the notes above). Local answers with Ollama are not routed and work as before. `REDIS_URL` stays on the zone's own Redis, which holds the rate limiter, setup token and error-log buckets; it never leaves the zone. Only masked text crosses the queue.

**Fetching the models for an offline host.** The air-gapped host has no internet access, so its model cache is filled on a connected machine and copied over. The pins live in the code, so both machines must run the same Septum checkout. On the connected machine, from that checkout:

```bash
docker compose -f docker-compose.airgap.yml build api
docker run --rm -v septum_septum-models:/app/models septum-api:local python -m septum_api.cli.models fetch --whisper base
docker run --rm -v septum_septum-models:/src -v "$PWD":/dst alpine tar czf /dst/septum-models.tar.gz -C /src .
```

Run the fetch with `docker run`, not `docker compose run`. `compose run` joins the internal `zone` network, which has no route out, so the download would fail there.

The `fetch` command downloads and verifies every default model at its pinned commit, then the Whisper model named by `--whisper` (repeat the option for each size you use). Without `--whisper` it fetches the `WHISPER_MODEL` setting, which defaults to `base`. The last command archives the models volume; the volume name carries the Compose project prefix (`septum_` for a checkout in a directory named `Septum`). The `docker run` fetch does not read the compose file, so the image's default online mode applies. This is the only step that downloads, and it runs on the connected host. Copy `septum-models.tar.gz` to the air-gapped host, then:

```bash
docker compose -f docker-compose.airgap.yml build api
docker compose -f docker-compose.airgap.yml create
docker run --rm -v septum_septum-models:/dst -v "$PWD":/src alpine tar xzf /src/septum-models.tar.gz -C /dst
docker compose -f docker-compose.airgap.yml up -d
docker compose -f docker-compose.airgap.yml run --rm api python -m septum_api.cli.models fetch --whisper base --offline-check
```

`--offline-check` verifies the copied files without touching the network. It prints one line per model and exits with status 1 if any file is missing or does not match its pin.

Both hosts need the `alpine` image locally: pull it on the connected machine and transfer it with `docker save` and `docker load`. Any image that contains `tar` can replace it in the commands above. The air-gapped compose file sets `HF_HUB_OFFLINE=1` on the `api` and `mcp` services, so those containers do not download models. The `mcp` service, when its profile is enabled, mounts the models volume read-only, so it only loads models that are already in the volume and pass the hash check. Pre-populate the volume with the `fetch` command (all default pins) before relying on `mcp` for languages that `api` has not loaded yet.

**Custom NER models.** A custom model is fetched on the connected host with `--user-pin`. Repeat the option for each custom model:

```bash
docker run --rm -v septum_septum-data:/app/data -v septum_septum-models:/app/models septum-api:local python -m septum_api.cli.models fetch --user-pin org/name@<40-character commit SHA>
docker run --rm -v septum_septum-data:/src -v "$PWD":/dst alpine cp /src/septum-user-pins.json /dst/
```

The user-pins file is `septum-user-pins.json` next to `config.json`, so it lives in the `septum-data` volume and not in the models volume. Copy it to the air-gapped host with the models archive. After the `create` step described above, and before `up -d`, restore it into the data volume:

```bash
docker run --rm -v septum_septum-data:/dst -v "$PWD":/src alpine cp /src/septum-user-pins.json /dst/
```

### 4. Internet-facing zone only

Runs only the gateway + audit modules on a host that *can* reach the cloud LLM providers. Consumes masked requests from Redis and forwards them to Anthropic / OpenAI / OpenRouter.

```bash
cp .env.example .env
# Set the four queue passwords: SEPTUM_QUEUE_API_PASSWORD, SEPTUM_QUEUE_GATEWAY_PASSWORD,
#   SEPTUM_QUEUE_AUDIT_PASSWORD, SEPTUM_QUEUE_HEALTH_PASSWORD
# Set SEPTUM_GATEWAY_REDIS_BIND to this host's private or VPN address (required; compose refuses to start without it)
# Set provider keys: ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.

docker compose -f docker-compose.gateway.yml up -d --build
```

This file runs the Redis that is the queue, plus the gateway, its health endpoint and the audit service. `REDIS_PASSWORD` is not used on this host, and the Redis default user is off. Each client has its own ACL user (see [Queue Redis users](#queue-redis-users)).

The gateway image does not contain `septum-core` by security invariant — it only sees placeholder-masked text and publishes masked answers back. The audit sidecar logs every request with entity types, counts, and regulation ids (never raw PII) and exposes `/api/audit/export` for SIEM ingestion.

**Air-gapped + internet-facing together**: two Docker hosts. The queue is the gateway host's Redis, published on `SEPTUM_GATEWAY_REDIS_BIND`. On the air-gapped host set `SEPTUM_GATEWAY_QUEUE_ADDR` to `<that address>:6379` and copy only `SEPTUM_QUEUE_API_PASSWORD`. Never copy the gateway, audit or health passwords to the air-gapped host. The Redis link is plain text (AUTH and masked payloads) and this file configures no TLS listener, so run it only over a VPN or private link. `rediss://` works in `SEPTUM_QUEUE_URL` when TLS is terminated on the Redis listener. The client verifies the certificate and the host name in the URL, so the certificate must name that host (for the air-gapped default, `zone-edge`).

### 5. Native / source install (contributors)

For development, debugging, or custom builds. Not recommended for production — use a compose variant instead.

```bash
git clone https://github.com/byerlikaya/Septum.git && cd Septum
./dev.sh --setup        # installs all six Python packages + npm deps
./dev.sh                # starts api + web on port 3000
```

Requirements: Python 3.11+ (3.13 tested), Node.js 22+ (the Docker images and CI build with Node 26), ffmpeg (for Whisper audio ingestion). The bootstrap script writes `config.json` with a freshly generated encryption key and JWT secret on first run; override with `SEPTUM_CONFIG_PATH`. Set `SEPTUM_DISABLE_MODEL_WARMUP=1` to skip the background model preload at startup; by default the NER, spaCy and embedding models are preloaded. With warmup disabled, the startup warning about stored model settings is skipped as well. `SEPTUM_USER_PINS_PATH` sets where the hashes of custom NER models are recorded; by default it is `septum-user-pins.json` next to `config.json` (`/app/data` in Docker). The `mcp` service does not mount the app data volume, because that volume holds `config.json` and the encryption key. So `mcp` cannot read a user-pins file from there and cannot use custom user-pinned NER models out of the box. To use one in `mcp`, give it a copy of the user-pins file in a read-only mount and point `SEPTUM_USER_PINS_PATH` at that copy.

**Bumping the model pins (maintainers).** `python packages/api/scripts/pin_models.py --write-manifest` reads the `main` commit of every default model from the Hugging Face Hub and regenerates `packages/core/septum_core/model_pins.py`; review the diff before committing it. `--check` re-reads each pinned commit and exits with status 1 on any drift. Both need network access.

Run the test suites:

```bash
pytest packages/*/tests -q        # all 7 modules
cd packages/web && npm test       # frontend Jest suite
```

---

## First launch — setup wizard

After any compose variant finishes booting, navigate to **http://localhost:3000**. You'll land on the setup wizard — it runs once, on a fresh install, and configures everything the backend needs to start. Its first step asks for the setup token.

| Step | What you do | Notes |
|---|---|---|
| **1. Database** | Pick SQLite (default) or PostgreSQL, test the connection | Compose variants pre-wire PostgreSQL; standalone defaults to SQLite |
| **2. Cache** | Pick in-memory (default) or Redis, test the connection | Compose variants pre-wire Redis; standalone uses in-memory |
| **3. LLM provider** | Ollama (local, bundled with full stack) / Anthropic / OpenAI / OpenRouter | See [LLM providers](#llm-providers) for key acquisition |
| **4. Regulations** | Tick the regulation packs you need (GDPR + your country, typically) | You can change this anytime via Settings → Regulations |
| **5. Audio model** | Whisper size (tiny / base / small / medium / large) | Skip if you won't ingest audio; downloads lazily on first audio upload |
| **6. Admin account** | Email + password for the first admin user | Becomes the only admin; create further accounts from the dashboard |

When the wizard finishes it writes `config.json`, creates the admin user, and redirects to the dashboard. The wizard is one-shot; hitting `/api/setup/*` after completion returns 403.

### Exact values to paste (full compose stack)

If you started with `docker compose up` (the full stack), the api container reaches the bundled PostgreSQL, Redis, and Ollama over the compose network — not over `localhost`. Use the compose service names as hostnames:

| Step | Field | Paste this (replace `<…>` with your `.env` value) |
|---|---|---|
| **1. Database → PostgreSQL** | URL | `postgresql+asyncpg://septum:<POSTGRES_PASSWORD>@postgres:5432/septum` |
| **2. Cache → Redis** | URL | `redis://:<REDIS_PASSWORD>@redis:6379/0` |
| **3. LLM provider → Ollama (bundled)** | URL | `http://ollama:11434` |

`<POSTGRES_PASSWORD>` / `<REDIS_PASSWORD>` are the values you set in `.env` at quickstart. Recover them with `grep -E 'POSTGRES_PASSWORD\|REDIS_PASSWORD' .env`. Note that the Redis URL has an empty user name between `://` and `:` — Redis's implicit `default` user; the colon-prefix before the password is required.

**Bringing your own Redis?** Run it with `maxmemory-policy noeviction` (the Redis default, which the bundled compose Redis uses). Erasing a document leaves a short-lived tombstone in Redis that stops other workers from serving a cached copy of its anonymization map; any eviction policy — `allkeys-*` or `volatile-*`, since the tombstone carries a TTL — could drop it early.

**Already have Ollama running on the host?** Stop the bundled one (`docker compose stop ollama && docker compose rm -f ollama`) so the port does not conflict, then use `http://host.docker.internal:11434` as the Ollama URL — docker-compose.yml wires `host.docker.internal` into the api container via `extra_hosts`, so it resolves back to your host's Ollama without any extra setup. The air-gapped file does not do this: its api reaches only the in-zone `ollama` service (`http://ollama:11434`), and `host.docker.internal` does not resolve there.

**Ollama URL rule.** Septum accepts an Ollama URL only when its host is loopback (`127.0.0.0/8`, `::1`), `localhost`, `host.docker.internal`, `host.containers.internal`, or `ollama` (the bundled compose service). Any other host is refused with HTTP 400 (`ollama_url_not_allowed`), including LAN or private IP addresses, `.local` names, and remote servers, even over `https://`, because Ollama receives unmasked text and raw PII must never leave the machine. `OLLAMA_BASE_URL` is copied into settings at first setup and used whenever no URL is saved; after that the saved URL governs. The same rule covers that variable: a refused value is not replaced by the default, so Ollama calls are refused until it is corrected. A custom compose service name for Ollama is not accepted. The names `ollama`, `host.docker.internal` and `host.containers.internal` are trusted by name, so do not alias them to another machine in `/etc/hosts`, DNS or container networking; Septum checks the name, not where it resolves. If a previously saved URL is now refused, Ollama features stay off, and the settings page flags it under the URL field until it is changed.

Each step has a **Test connection** button — it runs inside the api container, so it reflects what the backend will see after you finish the wizard. Green check = safe to move on.

---

## Access and network settings

### Setup token

While setup is open, the API creates a setup token on first start and prints it on every start, to its log:

```
Septum setup token: <token>
```

Read it with `docker compose logs api` (full stack), `docker compose -f docker-compose.airgap.yml logs api` (air-gapped file), `docker compose -f docker-compose.standalone.yml logs septum` (standalone), or, for source installs, the terminal running `./dev.sh`. The wizard asks for it in its first step, and every later setup request sends it. The token is deleted when the first admin account is created. Until then, anyone who can read the container log or `config.json` (the data volume) can complete setup, so treat that log and the data volume as sensitive.

To choose the value yourself, set `SEPTUM_SETUP_TOKEN` to at least 32 characters with at least 10 different characters. Septum refuses to start with a shorter or less varied value. Generate one with `openssl rand -hex 32`. When the variable is set, the log says `Septum setup is open; use the token set in SEPTUM_SETUP_TOKEN.` instead of printing the value.

An install that already has a user has no open setup, so no token is printed.

### Trusted proxies

The API reads a client's address from `X-Forwarded-For` only when the request comes from a listed proxy. The dashboard (Next.js) is that proxy and sets the header itself. `FORWARDED_ALLOW_IPS` therefore lists only the dashboard's address:

- Standalone: `127.0.0.1` (both processes share the container).
- Compose: the web container's fixed address, `SEPTUM_WEB_IP` (default `172.28.70.10`). The compose files set this for you.

`FORWARDED_ALLOW_IPS` never accepts `*`, and it refuses any range broader than `/8` (IPv4) or `/32` (IPv6).

If a reverse proxy sits in front of the dashboard, set `SEPTUM_TRUSTED_PROXIES` on the dashboard process (the web container's environment, or the host running `npm start`) to that proxy's address or CIDR, with the same size limits. The dashboard then reads the client address from the proxy's `X-Forwarded-For`. It refuses to start on an invalid entry. The full stack does not set this variable; add it to the `web` service's environment in that case. The air-gapped file sets it to the zone-edge relay's fixed address (`SEPTUM_ZONE_EDGE_IP`, default `172.28.70.11`), because the relay sits in front of the dashboard there and sets `X-Forwarded-For` to the real client address.

### Published ports

Only the dashboard (port 3000) listens on all interfaces. Every other published port binds to `127.0.0.1` unless you set its bind variable; the gateway Redis has no default and needs `SEPTUM_GATEWAY_REDIS_BIND`:

| Port | Service | Default bind | Variable |
|---|---|---|---|
| 3000 | Dashboard (web; zone-edge relay in the air-gapped file) | all interfaces | — |
| 8000 | API (full stack only) | `127.0.0.1` | — (not published in standalone or the air-gapped file) |
| 5432 | PostgreSQL (full stack only) | `127.0.0.1` | — |
| 11434 | Ollama (full stack only) | `127.0.0.1` | `SEPTUM_OLLAMA_BIND` |
| 8765 | MCP server (`mcp` profile) | `127.0.0.1` | `SEPTUM_MCP_BIND` |
| 8002 | Audit API | `127.0.0.1` | `SEPTUM_AUDIT_BIND` |
| 8001 | Gateway health | `127.0.0.1` | `SEPTUM_GATEWAY_HEALTH_BIND` |
| 6379 | Gateway Redis (gateway file) | required | `SEPTUM_GATEWAY_REDIS_BIND` |

The air-gapped file publishes no Redis, PostgreSQL or api port. Run commands inside those services with `docker compose -f docker-compose.airgap.yml exec <service> ...`. The full stack does not publish Redis. Ports published by Docker bypass the INPUT rules of ufw and firewalld. Use the bind variables above, and add DOCKER-USER rules where the host needs them.

### Queue Redis users

The gateway queue is a Redis with an ACL. The default user is off, and each client signs in as its own user, which can reach only its own streams and only the stream commands:

| User | Streams it can reach | Compose file |
|---|---|---|
| `septum-api` | request and response streams | air-gapped (through zone-edge), gateway, full stack |
| `septum-gateway` | request, response and audit streams | gateway, full stack |
| `septum-audit` | audit stream | gateway, full stack |
| `health` | `PING` only | gateway, full stack (healthcheck) |

Each user has a password from `.env`: `SEPTUM_QUEUE_API_PASSWORD`, `SEPTUM_QUEUE_GATEWAY_PASSWORD`, `SEPTUM_QUEUE_AUDIT_PASSWORD` and `SEPTUM_QUEUE_HEALTH_PASSWORD`. Generate them with `openssl rand -hex 32`; use hex only, because Redis' `--user` arguments cannot carry spaces or quotes. As with `requirepass`, the passwords are visible to anyone who can run `docker inspect` on the container, so restrict access to the Docker host.

### Gateway queue variables

| Variable | Set on | Meaning |
|---|---|---|
| `SEPTUM_GATEWAY_QUEUE_ADDR` | air-gapped host (`.env`) | `<gateway-private-ip>:6379`. The zone-edge relay forwards the api's queue connection there. Required by the air-gapped file. |
| `SEPTUM_QUEUE_URL` | api | Queue URL. Air-gapped default: `redis://septum-api:<SEPTUM_QUEUE_API_PASSWORD>@zone-edge:6379/0`; full stack default: the same user on `redis:6379`. Setting it on the air-gapped host replaces the default; keep it pointed at zone-edge. |
| `SEPTUM_QUEUE_DIR` | api, gateway | Alternative to `SEPTUM_QUEUE_URL`: a file-backed queue directory. The compose files use Redis. |
| `SEPTUM_ZONE_EDGE_IP` | air-gapped host (`.env`) | Fixed address of zone-edge on the `zone` network, default `172.28.70.11`. The dashboard trusts it as its proxy. |
| `SEPTUM_GATEWAY_REQUEST_TOPIC` / `SEPTUM_GATEWAY_RESPONSE_TOPIC` | api, gateway | Stream names. Defaults `septum.llm.requests` and `septum.llm.responses`. Both sides must match; the compose files set them. |
| `SEPTUM_GATEWAY_CLIENT_TIMEOUT_SECONDS` | api | How long the api waits for a gateway reply, in blocking reads of at most 25 s each, repeated until this deadline. Default `120`. Must be a positive number, or the api refuses to start when a queue is configured. |
| `SEPTUM_GATEWAY_TIMEOUT_SECONDS` | gateway | Total deadline for one upstream attempt, covering the whole request and not just the connection. Default `30`. |
| `SEPTUM_GATEWAY_MAX_ATTEMPTS` | gateway | Attempts per request. Default `3`. |
| `SEPTUM_QUEUE_REDIS_MAXMEMORY` | gateway host (`.env`), full stack | Memory cap of the queue Redis, in Redis size syntax (for example `512mb`). Default `256mb`. The policy is `noeviction`: when the cap is reached, new queue writes fail, so the request is not queued. Nothing already queued is evicted, and nothing is sent to a cloud provider directly. Size it for the expected backlog. |
| `SEPTUM_API_IP` | air-gapped host (`.env`) | Fixed address of the api on the `zone` network, default `172.28.70.12`. zone-edge relays the queue only from this address. Override it together with `SEPTUM_SUBNET`, `SEPTUM_DYNAMIC_IP_RANGE`, `SEPTUM_WEB_IP` and `SEPTUM_ZONE_EDGE_IP`. |

The api's client timeout must stay above the gateway's worst case. With the defaults that is 3 attempts × 30 s, plus two `Retry-After` waits capped at 10 s each, about 110 s. The default client timeout of 120 s covers it. When you raise `SEPTUM_GATEWAY_TIMEOUT_SECONDS` or `SEPTUM_GATEWAY_MAX_ATTEMPTS`, raise `SEPTUM_GATEWAY_CLIENT_TIMEOUT_SECONDS` to match, or the api gives up on answers the gateway still delivers.

**Queue retention.** With the Redis backend, each queue entry is acknowledged and deleted (XACK, then XDEL) once it is processed, so the queue holds only the backlog. With the file backend, processed messages move to `done/` under `SEPTUM_QUEUE_DIR`, and rejected or corrupted messages move to `dead-letter/`. An entry larger than 16 MiB, or one that cannot be decoded (JSON nested too deeply, or an integer literal past the digit limit), is handled the same way: the file backend moves it to `dead-letter/`, and the Redis backend acknowledges and deletes it, logging only its id and the reason. Dead-letter file names on the file backend are unique, so earlier evidence is never overwritten. A directory fault (an unwritable, missing or full queue directory) stops the worker with an error and leaves the messages in place; they are not moved to `dead-letter/`. The backend never trims either directory; if you use the file backend, remove old files from them yourself. The queue Redis runs with persistence disabled (`--save ""`, `--appendonly no`), so it writes no snapshot or append-only file. On a queue Redis restart, in-flight requests fail and the caller gets an error. The queue Redis also runs with `--maxmemory ${SEPTUM_QUEUE_REDIS_MAXMEMORY}` and `--maxmemory-policy noeviction`. When it is full, new writes are refused and new requests fail closed; queued messages are never evicted. Size the cap with `SEPTUM_QUEUE_REDIS_MAXMEMORY`.

**Clock sync.** In the split deployment (air-gapped and internet-facing hosts), both hosts must keep their clocks synchronised, through NTP or an internal time source. A skew of several minutes makes the api discard valid gateway replies as stale; the request then times out and falls back to Ollama.

### Monitoring

The dashboard no longer proxies `/metrics`. Scrape the API directly: `api:8000` from inside the compose network, or `127.0.0.1:8000` on the host. The standalone container does not publish port 8000, and neither does the air-gapped file. In standalone, publish it yourself with `127.0.0.1:8000:8000` in a compose override. In the air-gapped file, scrape from inside the zone, for example with `docker compose -f docker-compose.airgap.yml exec api`. `/metrics` needs no login, so never publish it on a routable address. With `WEB_CONCURRENCY` above 1, each scrape reads one worker's counters.

New Prometheus series:

- `septum_rate_limit_storage_up`: 1 while the rate-limit storage answers.
- `septum_unauthenticated_rejections_total`: 401 and 403 answers to callers with no login or API key, by status.
- `septum_password_work_refused_total`: login, register or password-change requests refused with 503 because the password queue was full.
- `septum_error_log_writes_dropped_total`: error log rows not written because the caller's budget was spent.
- `septum_error_log_write_failures_total`: error log rows that could not be stored.
- `septum_error_log_retention_failures_total`: error log retention runs that failed; they are retried on the next run.

`GET /health` also reports `rate_limit_storage`: `ok`, or `degraded` while the storage is unreachable.

### Error log retention

`ERROR_LOG_RETENTION_DAYS` (default `90`) deletes older error log rows once a day. `0` keeps them indefinitely and logs a warning at startup. Answers 401 or 403 to a caller with no login or API key are counted but not stored, so they cannot fill the table. Other rows draw on a budget per user, API key or client address: 60 a minute, or 20 a minute for server errors. Rows past the budget are counted only. Rows record the route template (for example `/api/documents/{document_id}`), not the concrete URL.

### Audit event retention

Audit events in the API database have no age limit. Deleting a document deletes its events; events not tied to a document stay. Events hold entity types, counts, masked placeholders and language codes, and the id of the document they concern, not its file name (the audit view resolves the current name when it is read, or shows "Deleted document #id", only for events that outlive their document). Events that record the acting user (a language override change stores the user id) keep the event but drop the id when that user is deleted. Septum does not prune or export these events on its own; if your policy needs a time limit, prune the `audit_events` table on your own schedule.

Upgrading removes file names from older events. On PostgreSQL, run `VACUUM FULL audit_events` after upgrading to rewrite the table without the old row versions (it locks the table while it runs); backups and WAL archives taken before the upgrade still contain the names.

The `septum-audit` service keeps its own JSONL ledger, separate from the events above. `SEPTUM_AUDIT_RETENTION_DAYS` and `SEPTUM_AUDIT_RETENTION_MAX_RECORDS` prune that live ledger file only. The worker prunes at startup and then once per effective period. The period runs from the start of one pass to the start of the next and equals `max(SEPTUM_AUDIT_RETENTION_INTERVAL_SECONDS, 10 x the duration of the last pass)`; the interval defaults to `3600`. Unset values keep everything, so set a cap if your policy needs one. A value below 1 stops the worker at startup. If the consumer or the retention task stops unexpectedly, the process exits with an error so the container restarts.

What is and is not covered:

- Only the live `audit.jsonl` is pruned, and only its oldest contiguous records. Rotated files, backups or snapshots, files downloaded from `/api/audit/export`, and copies sent to a SIEM or Splunk need their own retention. Queue storage is covered in Queue retention above.
- Records are aged by `received_at`, the time the sink received them. The sink stamps it, the hash covers it, and a producer cannot set it. Ledgers written before `received_at` existed fall back to `timestamp`. A legacy line with no usable age (a missing, zero or non-finite `timestamp`) takes the age of the next record that has one: it is removed exactly when that record is removed, and kept when no such record follows.
- The bound: except legacy lines with an unknown age (see above), a record is kept for at most `SEPTUM_AUDIT_RETENTION_DAYS` plus one effective interval plus one pass duration, and only while passes succeed. In the states `halted`, `failed`, `blocked` or `stale` the bound does not hold, and `/health` reports the state.
- Clock pin: when the age-cap is set and the oldest record with a known age is stamped more than 300 seconds ahead of the clock, the state is `blocked` once that record becomes the oldest with a known age, and pruning stops at it, because it is never older than the cutoff. Nothing after it is pruned until the clock catches up. The count cap still applies to a pinned ledger. Clock skew of up to 300 seconds is not flagged. The remedy depends on the cause:
  - Forward clock jump: fix the clock. Then either wait until real time passes the stamped time (pruning resumes by itself), or restore the ledger and its anchor from a copy that passes `JsonlFileSink(path).verify()`.
  - Backward NTP step: records stamped after the step stay behind larger earlier stamps until those expire. Fix the clock. Then either wait for them to expire, or restore from a verified copy and accept the reset.
  - A forged or mistaken future-dated legacy line prepended to a ledger without an anchor: restore from a verified copy taken before the line was added.
- The count cap is not a hard ceiling. It prunes only when the ledger exceeds `SEPTUM_AUDIT_RETENTION_MAX_RECORDS` by 5 % (at least one record), then trims it to exactly the cap. Between two passes the ledger can hold that slack above the cap, plus whatever is appended during the gap.
- Each prune that removes records first writes `audit.jsonl.anchor`. It holds the sha256 digest of the last pruned record and the hash the ledger started from before this prune (the previous anchor's hash, or 64 zeros on the first prune). The ledger verifies against either hash, so a crash between the anchor write and the ledger replace still verifies. It also holds a cumulative count of pruned records (a torn fragment is not counted) and the prune time. It never holds record content. Verification starts from the anchor, so a pruned ledger still verifies.
- The chain is unkeyed SHA-256 and the anchor is unsigned. Anyone with write access to the ledger directory can delete the oldest records and write a matching anchor, and verification will still pass. For real tamper resistance, ship the ledger and anchor off the host through the exporter or a SIEM. Keyed signing is not implemented.
- A hash chain cannot detect tail truncation, or restoring an older ledger together with its matching anchor. A copy taken just before the most recent prune still verifies against the current anchor. Protect both files with file permissions, and keep an external copy if you need stronger guarantees.
- Ledgers written before v1.2.0 may start with lines that have neither `prev_hash` nor `hash` (the format of v1.0.0 and v1.1.0). That leading segment is accepted only while no anchor exists, or while the anchor is a genesis anchor with `legacy_prefix` set. Such lines are pruned under the age rule above. A hash-less line that carries `received_at`, or any hash-less line after a chained record, halts retention.
- Nothing is dropped for its content. `attributes` is normalised before it is hashed: keys become strings (nested keys too, because the serialised form makes them strings), a list of two-item pairs becomes a dict, any other non-mapping value becomes `{"value": ...}`, and at the top level colliding keys are kept as a list of pairs under `"value"` (a nested collision keeps the last value). A value that cannot be serialised becomes `"<unserialisable>"`, and a value nested deeper than 32 levels becomes `"<depth limit>"`. Non-finite numbers become the strings `NaN`, `Infinity` or `-Infinity`. A timestamp that is zero, a boolean, a string, missing, out of range or non-finite is replaced with the ingest time, and the original is not kept. Each of these sets the record-level attribute `ingest_replaced` to `true`, which the hash covers. The flag says only that a value in this record was replaced at ingest, not which field. Only the sink sets it, and the sink drops any producer copy before it writes the record. The same holds for `ingest_unstorable`, which marks a placeholder the sink stored in place of a record it refused. A record the sink cannot store as received (its line would not verify when read back) is kept as a placeholder with its id (cut to 64 characters), source, event type and correlation id, with `attributes` set to `{"value": "<unstorable>"}`; its content is not stored. Its `attributes` also carry the `ingest_unstorable` and `ingest_replaced` markers. Lines written before this normalisation, which hold `NaN` or `Infinity` literals, still verify with the serialiser that wrote them. The consumer acknowledges and logs a payload that cannot be parsed into a record at all as malformed; nothing is written. Any other failure requeues the message, and retries back off from 2 seconds, doubling each time, capped at 60 seconds.
- Exports render non-finite numbers as strings in all three formats (JSON, CSV and SIEM), so the JSON and SIEM output stays strict JSON. An export that fails part way through is logged by exception type, and the transfer is aborted so the client sees an incomplete body. A failure before the first record is returned as HTTP 500. Export lines whose non-finite values were rendered as strings (legacy NaN lines) do not re-verify with `verify_chain`.
- Lines with duplicate or unknown fields are never accepted as records. Every chained line must be byte-identical to its canonical serialisation. A torn final line left by a crash is truncated before the next append, unless it is a complete record whose newline was lost; then the newline is restored. A torn tail also forces the next pass to run in full.
- Delivery is at-least-once. If an append is durable but a later step fails (for example, both the fsync and the truncate fail), the consumer redelivers the message and the ledger can hold a second record with the same `id`. The chain stays valid, and the duplicate is found by its `id`.

Halted retention: before a pass that can remove records, the worker verifies the whole chain. A malformed line (including one that is too deeply nested or holds a number out of range), a duplicate or unknown field, a non-canonical line, a broken link or a `received_at` that is not finite stops pruning: nothing is removed, the worker logs `audit retention halted until the ledger is repaired` at error level on every tick, and `/health` reports `halted`. To recover, find the line number in the log, and restore `audit.jsonl` and `audit.jsonl.anchor` from a copy that passes `JsonlFileSink(path).verify()`; retention resumes on the next tick. A pass that is skipped because nothing can expire does not re-verify the chain, so a halt surfaces on the first pass that needs to prune.

Retention status: one worker process writes a given ledger, and the status describes that process only. Every tick, the worker writes `audit.jsonl.status` with `state` (`disabled`, `ok`, `halted`, `failed` or `blocked`), `last_success_at`, `consecutive_failures`, `checked_at` and `interval_seconds` (the effective period). `blocked` is written only when an age cap is set. While retention is disabled, a heartbeat task in the same process writes `disabled` each period. The status holds no record content. `ok` means the last retention pass succeeded; it does not mean the ledger was verified. Until the first pass ends there is no status file yet, so `/health` reports `not_reported` within its first window. `GET /health` on the audit API reports `retention`:

- `ok` and `disabled`: 200.
- `halted`, `failed` and `blocked`: 503 with `status: degraded`.
- `stale`: `checked_at` is older than twice `interval_seconds` plus five minutes. 503. A worker that has stopped, or whose consumer or retention task has died, stops writing, and this state follows.
- `missing`: no status file, and the API has been up for longer than that window. 503. Every worker writes a status, so a missing file means no worker is running or it cannot write.
- `unreadable`: the file exists but cannot be read or is malformed. 503.
- Consumer state, from its heartbeat sidecar: `consumer_failing` means three or more consecutive failures, or a failure streak older than five minutes. 503. `consumer_unreadable` means the sidecar is malformed. 503. A missing consumer sidecar is healthy.
- `not_reported`: no status file yet, within the first window after the API starts. 200.

The image HEALTHCHECK treats any non-2xx as unhealthy, so the status code alone surfaces these states. The audit API mounts the ledger volume read-only: it only reads the ledger and its sidecars. A `failed` state clears at the next successful pass, which can be a full interval later.

### Gateway routing and fail-closed behaviour

When gateway routing is on, cloud LLM calls go through the gateway queue and never directly from the API. Routing is on when the stored `use_gateway` setting is on, or when `USE_GATEWAY_DEFAULT=true` forces it. The environment variable wins over a stored off: it is read on every call and nothing is written to the database. Setting `USE_GATEWAY_DEFAULT` to false or leaving it unset makes the stored value decide, as before. The API applies these rules:

- `USE_GATEWAY_DEFAULT=true` with neither `SEPTUM_QUEUE_URL` nor `SEPTUM_QUEUE_DIR` set: the API refuses to start.
- Routing on but no gateway client installed (for example, `USE_GATEWAY_DEFAULT=true` or a saved `use_gateway` on, with no queue configured): the call fails with the code `gateway_not_configured`. There is no direct cloud call, no Ollama fallback, and the circuit breaker is not touched. Chat returns the coded error on its stream, and the LLM settings test returns HTTP 400 with the same code.
- A real gateway failure, such as a timeout, an upstream error or a missing provider key on the gateway, is handled like a direct cloud failure: it counts against the circuit breaker and the call falls back to Ollama.
- `llm_provider=ollama` always runs locally, whatever `use_gateway` or `USE_GATEWAY_DEFAULT` says.

Boolean `*_DEFAULT` flags, such as `USE_GATEWAY_DEFAULT`, accept only `1`, `true`, `yes`, `on` and `0`, `false`, `no`, `off`, in any case. A blank value means the default. All of them are checked when the api starts. Any other value is refused with an error that names the variable and never shows its value. Integer `*_DEFAULT` flags must be whole numbers from each flag's minimum up to 2147483647, and `RAG_RELEVANCE_THRESHOLD_DEFAULT` must be a finite number from 0.0 to 1.0. An invalid value in any boolean or number `*_DEFAULT` flag, including `USE_GATEWAY_DEFAULT`, stops api startup.

The stored `use_gateway` value is copied from `USE_GATEWAY_DEFAULT` when setup completes. The dashboard does not show it.

## LLM providers

Septum can use any combination of the following:

| Provider | Where to get a key | When to use |
|---|---|---|
| **Ollama** (local) | Bundled in the full stack; for standalone install from [ollama.com](https://ollama.com) | Semantic PII layer, Auto-RAG classifier, free-tier local chat |
| **Anthropic** | [console.anthropic.com](https://console.anthropic.com) | Claude Opus / Sonnet / Haiku — highest-quality answers |
| **OpenAI** | [platform.openai.com](https://platform.openai.com) | GPT-4, GPT-4 Turbo, GPT-4o |
| **OpenRouter** | [openrouter.ai](https://openrouter.ai) | Unified API across 100+ models with a single key |

**Where the text goes.** A cloud provider receives only the masked messages. Masking can miss identifiers, so treat the text as possibly containing residual personal data. If a cloud call fails, the local Ollama model receives the same masked messages. The provider is normally a processor or recipient for the masked prompt, which is a contractual question for you. Whether sending it there is an international transfer depends on where the provider processes requests relative to your jurisdiction. Deciding whether the transfer is permitted, and what safeguards apply, is the operator's responsibility. The Ollama fallback uses the configured Ollama address; if that is not on your own host or network, it is a recipient too.

**Ollama model recommendations:**

- `llama3.2:3b` — 2 GB, fast, good enough for Auto-RAG classification
- `aya-expanse:8b` — 4.7 GB, **recommended** for semantic PII detection; benchmark default
- `qwen2.5:14b` — 8.4 GB, better quality if you have the RAM

Pull additional models anytime:

```bash
docker compose exec ollama ollama pull aya-expanse:8b
docker compose exec ollama ollama list     # see what's downloaded
```

The active model for chat vs semantic detection is chosen from the Settings → LLM tab after the wizard.

---

## Data persistence & volumes

The compose files declare named Docker volumes so your data survives container restarts and rebuilds. The standalone file uses the fixed names below; the full stack (`docker-compose.yml`) and the air-gapped file prefix them with the compose project name (for example `septum_septum-data`).

| Volume | Path inside container | Content |
|---|---|---|
| `septum-data` | `/app/data` | SQLite DB (if used), `config.json`, encryption keys |
| `septum-uploads` | `/app/uploads` | Uploaded original files, AES-256-GCM encrypted |
| `septum-anon-maps` | `/app/anon_maps` | Per-document PII placeholder mappings, encrypted |
| `septum-vector-indexes` | `/app/vector_indexes` | FAISS embeddings |
| `septum-bm25-indexes` | `/app/bm25_indexes` | BM25 keyword indexes |
| `septum-models` | `/app/models` | Cached HuggingFace, Whisper and PaddleOCR models |
| `septum-pgdata` | `/var/lib/postgresql/data` | PostgreSQL cluster (compose only) |
| `septum-redis-data` | `/data` | Air-gapped stack's local Redis (rate limits, cache) |
| `ollama-data` | `/root/.ollama` | Downloaded Ollama models (full stack and air-gapped file) |
| `septum-audit-log` | `/var/log/septum` | Audit log files (full stack and gateway file) |
| `septum-mcp-scan` | `/data/scan` | MCP scan directory, mounted read-only (full stack and air-gapped file) |

**Backup.** A consistent backup means two things: a PostgreSQL dump *and* the encrypted volumes (uploads + anon-maps + vector/BM25 indexes). The encryption key lives in `septum-data/config.json` — lose it and every encrypted volume becomes unreadable. A database dump contains the extracted document text, so protect backups like the data itself.

```bash
# Dump Postgres (full stack only)
docker compose exec -T postgres pg_dump -U septum septum > septum-$(date +%F).sql

# Tar the named volumes (works for standalone and the full stack)
for v in data uploads anon-maps vector-indexes bm25-indexes models; do
  name=$(docker volume ls -q --filter "label=com.docker.compose.volume=septum-$v" | head -n 1)
  name=${name:-septum-$v}
  docker volume inspect "$name" >/dev/null 2>&1 || { echo "volume $name not found, skipped" >&2; continue; }
  docker run --rm -v "$name":/src -v "$PWD":/dst alpine \
    tar czf /dst/septum-$v-$(date +%F).tar.gz -C /src .
done
```

The loop looks up the real volume name from the label compose puts on it, so the project prefix does not matter. Run it with only one Septum stack on the host.

**Restore** is the reverse. Stop the stack, `docker volume rm` the existing volumes, recreate + extract, start.

---

## Upgrading

Septum follows semver. Minor versions never break configs; major versions are called out in the [Changelog](../changelog.md) with explicit migration notes.

```bash
git pull                                    # compose file changes occasionally
docker compose up -d --build                # rebuild the images and recreate the containers
docker compose logs -f api                  # watch the migration run
```

Alembic migrations apply automatically at api boot. If a migration fails, the api container exits with a descriptive error — inspect the logs, resolve the cause, retry the `up -d --build`.

For the standalone variant, run the same command with its compose file:

```bash
git pull
docker compose -f docker-compose.standalone.yml up -d --build
```

Volumes are preserved across the rebuild because they are named volumes.

**Breaking changes in this release.**

- Setup now needs the setup token from every address, including `localhost`. The loopback exception is gone, so a setup request without the token gets `403`.
- The API and the other service ports bind to `127.0.0.1` by default (see [Published ports](#published-ports)). Reach the API through the dashboard on port 3000.
- Compose uses a fixed subnet (`172.28.70.0/24` by default; `SEPTUM_SUBNET`, `SEPTUM_DYNAMIC_IP_RANGE` and `SEPTUM_WEB_IP` change it). If compose reports that the network changed, run `docker compose -f <file> down` once with the same `-f` file you start with (for example `docker compose -f docker-compose.airgap.yml down`); volumes are kept. Then start again with `docker compose -f <file> up -d --build`. A custom `SEPTUM_SUBNET` needs `SEPTUM_WEB_IP` inside it and outside `SEPTUM_DYNAMIC_IP_RANGE`.
- A `rate_limit` of `60/minute` in `config.json` (the value earlier releases wrote) is read as the new default, `600/minute`.
- `docker-compose.gateway.yml` no longer starts until `SEPTUM_GATEWAY_REDIS_BIND` is set (Redis used to publish on all interfaces).
- The default rate limit is now enforced on every API route; earlier releases never applied it.
- **Gateway queue, zone split.** The air-gapped file no longer publishes the api (8000), PostgreSQL or Redis ports. Use `docker compose -f docker-compose.airgap.yml exec <service> ...` instead.
- **Gateway queue, air-gapped host.** The air-gapped file needs `SEPTUM_GATEWAY_QUEUE_ADDR` and `SEPTUM_QUEUE_API_PASSWORD` in `.env`. Compose refuses to start without them. Cloud calls from this zone go through the gateway queue.
- **Ollama on the air-gapped host.** The api reaches Ollama as `http://ollama:11434`, a container inside the zone. The host Ollama relay is gone. A stored Ollama URL of `host.docker.internal` no longer works there; change it in Settings. Models are loaded with `ollama-pull` (see [variant 3](#3-air-gapped-zone-only)).
- **Pre-fetching models.** The air-gapped host's models are fetched with `docker run` on a connected machine, outside the zone. Use the commands in [variant 3](#3-air-gapped-zone-only). `docker compose run` no longer works for this, because it joins the internal network.
- **Gateway host.** The gateway Redis's default user is off, and `REDIS_PASSWORD` is not used on this host. Queue URLs in the old form `redis://:<password>@...` stop working; use the per-service URLs the compose file builds from the four `SEPTUM_QUEUE_*_PASSWORD` values.
- **Full stack.** `docker-compose.yml` needs the four `SEPTUM_QUEUE_*_PASSWORD` values in `.env`. Compose refuses to start without them.
- **Existing air-gapped installs.** With `USE_GATEWAY_DEFAULT=true` in the file, gateway routing is forced whatever the stored `use_gateway` setting says. An existing air-gapped install whose stored `use_gateway` is false is now forced through the gateway: its cloud calls stop going directly and go through the queue. Until the gateway host is running and the queue variables are set, those calls get `gateway_not_configured`. The stored value itself is not changed, so setting `USE_GATEWAY_DEFAULT` to false restores the stored behaviour.
- **Timeouts.** Raising `SEPTUM_GATEWAY_TIMEOUT_SECONDS` requires raising `SEPTUM_GATEWAY_CLIENT_TIMEOUT_SECONDS` to match. `SEPTUM_GATEWAY_TIMEOUT_SECONDS` is now a total deadline per attempt. Earlier releases used httpx per-phase timeouts, which did not bound a slow response.
- **Startup refusal.** With `USE_GATEWAY_DEFAULT=true`, the api refuses to start unless `SEPTUM_QUEUE_URL` or `SEPTUM_QUEUE_DIR` is set.
- **Queue Redis data volume.** The queue Redis no longer has a data volume. Existing installs should remove the old queue Redis data volume before starting, because it still holds old queue data. On the gateway host, `<project>_septum-gateway-redis-data` can always be removed. On a full dev stack host, remove `<project>_septum-redis-data` only if that host does not also run the air-gapped stack from the same project directory, because both stacks use that volume name. `<project>` is the Compose project name.

**Model caches after an upgrade.** Model caches from an earlier version may not match the pinned revisions. On the next online start, a pinned snapshot that holds leftover unpinned files is purged and fetched once; offline, it is refused until it is fetched again. An air-gapped install must run the `fetch` command again on a connected host and copy the models volume and the user-pins file, as described in [variant 3](#3-air-gapped-zone-only). A stored bare custom NER id is refused with `ner_model_unpinned`. A stored Whisper model name outside the openai-whisper registry (such as a local checkpoint path) is refused with `whisper_model_not_allowed`. Both must be changed in Settings. Unless warmup is disabled, the startup log then warns, with error codes only, about each stored custom NER model or Whisper model that would be refused.

**Coming from the published `byerlikaya/septum` image.** Septum no longer publishes container images; the older images on Docker Hub remain available but are not updated. Remove the old container only, then build and start the standalone compose file. The container removal keeps the volumes, and the compose file uses the same volume names (`septum-data`, `septum-uploads`, `septum-anon-maps`, `septum-vector-indexes`, `septum-bm25-indexes`, `septum-models`), so your data, `config.json` and encryption key are reused.

```bash
docker rm -f septum
git clone https://github.com/byerlikaya/Septum.git && cd Septum
docker compose -f docker-compose.standalone.yml up -d --build
```

If you already ran the standalone compose file before it used fixed volume names, your data sits in prefixed volumes (for example `septum_septum-data`). Copy each one once into the fixed-name volume, repeating for every volume in the table above:

```bash
docker run --rm -v septum_septum-data:/from -v septum-data:/to alpine cp -a /from/. /to/
```

Never run `docker volume prune` while moving: it deletes every volume no container currently uses, including the ones that hold your data.

---

## Troubleshooting

**Wizard hangs at "Testing Redis / PostgreSQL connection"**. The compose file requires `POSTGRES_PASSWORD` and `REDIS_PASSWORD` — if they're missing in `.env`, the data containers didn't start. Check `docker compose logs postgres redis` and set the variables before restarting.

**Ollama step fails with "connection refused"**. Full stack: the Ollama container may still be pulling its first model; give it 30-60 seconds and retry. Standalone: Ollama is not bundled — either install it on the host (`brew install ollama`) and point the wizard at `http://host.docker.internal:11434`, or choose a cloud provider.

**The wizard says "The setup token is invalid or expired"**. Enter the token exactly as printed. The token stays the same across restarts until setup completes. If you set `SEPTUM_SETUP_TOKEN`, enter that value; with the variable set, the log line reads `Septum setup is open; use the token set in SEPTUM_SETUP_TOKEN.` Otherwise read the latest line with `docker compose logs api | grep 'setup token'` (standalone: `docker compose -f docker-compose.standalone.yml logs septum | grep 'setup token'`). If the log has no token line, setup is closed because the database already has a user. After 10 wrong tries in a minute the wizard reports too many attempts; wait a minute and try again.

**The api exits at startup with "USE_GATEWAY_DEFAULT is true but neither SEPTUM_QUEUE_URL nor SEPTUM_QUEUE_DIR is set".** This is the fail-closed startup check. Set the queue variables (see [Gateway queue variables](#gateway-queue-variables)) or unset `USE_GATEWAY_DEFAULT`. The air-gapped file cannot run without a queue.

**Chat or the LLM settings test shows `gateway_not_configured`.** A saved setting routes LLM calls through the gateway, but this api has no queue client. Septum does not fall back to a direct cloud call or to Ollama here. Configure the queue and make sure the gateway host is running. The dashboard does not show `use_gateway`; the stored value is copied from `USE_GATEWAY_DEFAULT` when setup completes.

**Port 3000 already in use**. Something else on your machine owns the port. Either stop the offender, or change Septum's binding: `docker compose up -d` with `-p 3001:3000` in a compose override, or edit `docker-compose.yml`'s `ports:` entry.

**Document ingestion fails with `disk I/O error` under parallel uploads**. SQLite's WAL has a single writer lock; Septum's pool is sized to 50 connections but heavy parallel OCR + Whisper workloads can still saturate. Move to PostgreSQL (compose variant already does this by default) or reduce the ingestion concurrency cap in Settings → Ingestion.

**Whisper model download times out on first audio upload**. The download happens lazily and can take several minutes for larger models. Pre-warm the model from the Settings → Ingestion tab before the first real upload, or run `docker compose exec api python -c "import whisper; whisper.load_model('base')"`.

**Docker Desktop says "insufficient resources"**. Raise the VM memory in Docker Desktop → Settings → Resources to at least 8 GB. Ollama + Presidio + Whisper together are RAM-heavy.

**Something else**. Check the relevant log stream:

```bash
docker compose logs -f api       # backend, pipeline, auth, audit
docker compose logs -f web       # Next.js build + runtime
docker compose logs -f ollama    # model downloads, GPU detection
docker compose logs -f postgres  # database init / migrations
```

If the cause isn't obvious, open a [GitHub issue](https://github.com/byerlikaya/Septum/issues) with the failing log excerpt, your compose variant, and the OS / Docker versions.

---

## Uninstalling

The cleanest removal:

```bash
docker compose down -v           # stops containers + removes named volumes
```

`-v` is the important flag — without it, volumes persist and a subsequent `docker compose up` resurrects your old data. If you want to keep the data "in case", omit `-v` and back the volumes up first (see [Data persistence](#data-persistence--volumes)).

For the standalone variant:

```bash
docker compose -f docker-compose.standalone.yml down -v
```

For the native install, remove the cloned directory and the Python / npm caches Septum wrote under `~/.cache/septum` (if any).

---

<p align="center">
  <a href="./index.md"><strong>🏠 Home</strong></a>
  &nbsp;·&nbsp;
  <strong>🚀 Installation</strong>
  &nbsp;·&nbsp;
  <a href="benchmark.md"><strong>📈 Benchmark</strong></a>
  &nbsp;·&nbsp;
  <a href="features.md"><strong>✨ Features</strong></a>
  &nbsp;·&nbsp;
  <a href="architecture.md"><strong>🏗️ Architecture</strong></a>
  &nbsp;·&nbsp;
  <a href="document-ingestion.md"><strong>📊 Document Ingestion</strong></a>
  &nbsp;·&nbsp;
  <a href="screenshots.md"><strong>📸 Screenshots</strong></a>
</p>
