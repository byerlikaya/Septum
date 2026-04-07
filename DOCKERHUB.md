<p align="center">
  <img src="https://raw.githubusercontent.com/byerlikaya/Septum/main/septum_logo.png" alt="Septum logo" width="180" />
</p>

<h3 align="center">Your data never leaves. Your AI still works.</h3>

<p align="center">
  <a href="https://github.com/byerlikaya/Septum"><img src="https://img.shields.io/badge/GitHub-Source-181717?logo=github" alt="GitHub" /></a>
  <a href="https://github.com/byerlikaya/Septum/blob/main/ARCHITECTURE.md"><img src="https://img.shields.io/badge/docs-Architecture-blue" alt="Architecture" /></a>
  <a href="https://github.com/byerlikaya/Septum/blob/main/CHANGELOG.md"><img src="https://img.shields.io/badge/docs-Changelog-blue" alt="Changelog" /></a>
  <a href="https://github.com/byerlikaya/Septum/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-view-green" alt="License" /></a>
</p>

---

**Septum** is a privacy-first AI middleware that sits between your documents and cloud LLMs. Upload sensitive documents, ask questions with any LLM — **no raw personal data ever leaves your machine**.

## How It Works

1. **Upload** documents (PDF, Word, Excel, images, audio).
2. **Septum detects and masks** all personal data locally.
3. **Only anonymised text** is sent to the LLM.
4. **Answers come back** with real values restored — locally.

## Quick Start

```bash
docker run --name septum \
  --add-host=host.docker.internal:host-gateway \
  -p 3000:3000 \
  -v septum-data:/app/data \
  -v septum-uploads:/app/uploads \
  -v septum-anon-maps:/app/anon_maps \
  -v septum-vector-indexes:/app/vector_indexes \
  -v septum-bm25-indexes:/app/bm25_indexes \
  byerlikaya/septum
```

Open **http://localhost:3000** — the setup wizard configures everything (database, cache, LLM provider). No `.env` file needed.

## Updating

```bash
docker stop septum && docker rm septum
docker pull byerlikaya/septum
docker run --name septum \
  --add-host=host.docker.internal:host-gateway \
  -p 3000:3000 \
  -v septum-data:/app/data \
  -v septum-uploads:/app/uploads \
  -v septum-anon-maps:/app/anon_maps \
  -v septum-vector-indexes:/app/vector_indexes \
  -v septum-bm25-indexes:/app/bm25_indexes \
  byerlikaya/septum
```

The `docker pull` step is required — `docker run` alone reuses the cached image. Your data is preserved in the named volumes.

## Docker Compose (PostgreSQL + Redis)

```bash
docker compose up
```

Add `--profile ollama` for a local Ollama instance.

See [`docker-compose.yml`](https://github.com/byerlikaya/Septum/blob/main/docker-compose.yml) for the full configuration.

## Key Features

- **Local PII Protection** — Documents encrypted (AES-256-GCM), raw data never leaves your machine
- **17 Built-in Regulations** — GDPR, KVKK, CCPA, HIPAA, LGPD, PIPEDA, PDPA, APPI, PIPL, POPIA, and more
- **Approval Gate** — Review exactly what gets sent to the LLM before it leaves
- **Custom Rules** — Regex, keyword lists, or LLM-prompt based detection
- **Multi-Provider** — Anthropic, OpenAI, OpenRouter, Ollama — switch from the UI
- **Rich Formats** — PDFs, Office files, images (OCR), audio (Whisper)
- **Hybrid Retrieval** — FAISS semantic search + BM25 keyword matching
- **Audit Trail** — Append-only compliance log, no raw PII in events

## Volumes

| Volume | Purpose |
|--------|---------|
| `/app/data` | Database, config, encryption keys |
| `/app/uploads` | Uploaded documents (encrypted) |
| `/app/anon_maps` | Anonymisation mappings (encrypted) |
| `/app/vector_indexes` | FAISS vector indexes |
| `/app/bm25_indexes` | BM25 keyword indexes |

## Ports

| Port | Service |
|------|---------|
| `3000` | Web UI + API (single port) |

All API endpoints (`/api/*`, `/docs`, `/health`) are served through port 3000. No need to expose port 8000.

## Links

- **Source Code:** [github.com/byerlikaya/Septum](https://github.com/byerlikaya/Septum)
- **Architecture:** [ARCHITECTURE.md](https://github.com/byerlikaya/Septum/blob/main/ARCHITECTURE.md)
- **Changelog:** [CHANGELOG.md](https://github.com/byerlikaya/Septum/blob/main/CHANGELOG.md)
- **Issues:** [GitHub Issues](https://github.com/byerlikaya/Septum/issues)
- **License:** [LICENSE](https://github.com/byerlikaya/Septum/blob/main/LICENSE)
