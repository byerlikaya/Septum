<p align="center">
  <img src="https://raw.githubusercontent.com/byerlikaya/Septum/main/assets/septum_logo.png" alt="Septum logo" width="180" />
</p>

<h3 align="center">Your data never leaves. Your AI still works.</h3>

<p align="center">
  <a href="https://github.com/byerlikaya/Septum"><img src="https://img.shields.io/badge/GitHub-Source-181717?logo=github" alt="GitHub" /></a>
  <a href="https://github.com/byerlikaya/Septum/blob/main/docs/ARCHITECTURE.md"><img src="https://img.shields.io/badge/docs-Architecture-blue" alt="Architecture" /></a>
  <a href="https://github.com/byerlikaya/Septum/blob/main/docs/BENCHMARK.md"><img src="https://img.shields.io/badge/docs-Benchmark-blue" alt="Benchmark" /></a>
  <a href="https://github.com/byerlikaya/Septum/blob/main/CHANGELOG.md"><img src="https://img.shields.io/badge/docs-Changelog-blue" alt="Changelog" /></a>
  <a href="https://github.com/byerlikaya/Septum/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="License" /></a>
</p>

---

**The all-in-one image.** Everything Septum needs in a single container: FastAPI backend, Next.js dashboard, SQLite by default, no external services required. Simplest way to try Septum.

## Quick start

```bash
docker run --name septum \
  --add-host=host.docker.internal:host-gateway \
  -p 3000:3000 \
  -v septum-data:/app/data \
  -v septum-uploads:/app/uploads \
  -v septum-anon-maps:/app/anon_maps \
  -v septum-vector-indexes:/app/vector_indexes \
  -v septum-bm25-indexes:/app/bm25_indexes \
  -v septum-models:/app/models \
  byerlikaya/septum
```

Open **http://localhost:3000** — the setup wizard picks database, cache, LLM provider, and regulations. No `.env` file needed.

## Tags

| Tag | Contents |
|---|---|
| `latest`, `1`, `1.0`, `1.0.0` | CPU variant, multi-arch (amd64 + arm64), ~2 GB |
| `gpu`, `1-gpu`, `1.0-gpu`, `1.0.0-gpu` | GPU variant with CUDA-enabled PyTorch, linux/amd64 only |

Use `-gpu` if you have an NVIDIA GPU available to the container — batch document ingestion, OCR, and Whisper transcription all become measurably faster.

## Volumes

| Path | Purpose |
|---|---|
| `/app/data` | SQLite database, config, encryption keys |
| `/app/uploads` | Uploaded documents (AES-256-GCM encrypted) |
| `/app/anon_maps` | Anonymisation mappings (encrypted) |
| `/app/vector_indexes` | FAISS vector indexes |
| `/app/bm25_indexes` | BM25 keyword indexes |
| `/app/models` | Cached HuggingFace / Whisper model files |

## Looking for the modular layout?

This all-in-one image bundles everything. For air-gap deployments, split zones, or custom orchestration, see the sibling images: [`septum-api`](https://hub.docker.com/r/byerlikaya/septum-api), [`septum-web`](https://hub.docker.com/r/byerlikaya/septum-web), [`septum-gateway`](https://hub.docker.com/r/byerlikaya/septum-gateway), [`septum-audit`](https://hub.docker.com/r/byerlikaya/septum-audit), [`septum-mcp`](https://hub.docker.com/r/byerlikaya/septum-mcp).

## Links

- **Source:** [github.com/byerlikaya/Septum](https://github.com/byerlikaya/Septum)
- **Architecture:** [docs/ARCHITECTURE.md](https://github.com/byerlikaya/Septum/blob/main/docs/ARCHITECTURE.md)
- **Benchmark:** [docs/BENCHMARK.md](https://github.com/byerlikaya/Septum/blob/main/docs/BENCHMARK.md)
- **Issues:** [github.com/byerlikaya/Septum/issues](https://github.com/byerlikaya/Septum/issues)
