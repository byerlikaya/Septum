# Contributing to Septum

<p align="center">
  <a href="README.md"><strong>🏠 Home</strong></a>
  &nbsp;·&nbsp;
  <a href="docs/FEATURES.md"><strong>✨ Features</strong></a>
  &nbsp;·&nbsp;
  <a href="docs/ARCHITECTURE.md"><strong>🏗️ Architecture</strong></a>
  &nbsp;·&nbsp;
  <a href="docs/DOCUMENT_INGESTION.md"><strong>📊 Document Ingestion</strong></a>
  &nbsp;·&nbsp;
  <a href="docs/SCREENSHOTS.md"><strong>📸 Screenshots</strong></a>
  &nbsp;·&nbsp;
  <strong>🤝 Contributing</strong>
  &nbsp;·&nbsp;
  <a href="CHANGELOG.md"><strong>📝 Changelog</strong></a>
</p>

---

> 🇹🇷 Bu belgenin [Türkçesi](CONTRIBUTING.tr.md).

Thanks for taking the time to contribute! Septum is privacy-first AI
middleware — we want every contribution to keep that bar.

## Ways to contribute

- **Report a bug** — open an issue with steps to reproduce, expected
  vs actual behaviour, and your environment (OS, Docker vs local,
  Python / Node versions if running locally).
- **Propose a feature** — open an issue with the `proposal` label
  describing the use case and rough API shape before writing a PR,
  so we can align on direction.
- **Submit a fix or feature** — fork the repo, branch off `main`,
  open a pull request against `main`.
- **Improve docs** — typo fixes, clearer explanations, missing
  examples are all welcome. READMEs and `docs/` live in English and
  Turkish; structural parity is required (same headings, same order,
  same links).

## Development setup

```bash
git clone https://github.com/<your-username>/Septum.git
cd Septum
./dev.sh --setup     # install all Python packages + frontend deps
./dev.sh             # start api + web on port 3000
```

Requirements:
- Python 3.11+ (tested up to 3.13)
- Node.js 20+
- ffmpeg (for Whisper audio ingestion)

First visit opens the setup wizard. No manual `.env` editing.

## Running tests

```bash
# Backend (all modules)
pytest packages/*/tests -q

# Single module
pytest packages/core/tests/ -q
pytest packages/api/tests/test_sanitizer.py -v

# Frontend
cd packages/web && npm test
```

All LLM calls in tests **must** be mocked — real cloud requests are
rejected by CI.

## Code style

- **Python:** `ruff check` + `ruff format` must pass. Type hints
  required on public APIs.
- **TypeScript:** `npm run lint` + `tsc --noEmit` must pass.
- **No country or language names** in class/function/variable names
  (see `CLAUDE.md` § Zero-Tolerance Generic Architecture). Exceptions:
  `national_ids/` algorithmic validators, ISO 639-1 codes in mapping
  tables, HuggingFace model IDs, regulation seed descriptions, tests.
- **No inline LLM prompts.** All prompts go through `PromptCatalog`
  in `packages/api/septum_api/services/prompts.py`.
- **Async everywhere.** All DB, file I/O, and LLM calls are async.

## Commit messages

Imperative, in English, conventional prefix with module scope:

```
<type>(<scope>): <description>

type: feat, fix, refactor, test, docs, chore
scope: core, mcp, api, web, queue, gateway, audit
```

Examples:
- `feat(core): add Australia Privacy Act recognizer pack`
- `fix(api): respect rag_relevance_threshold for empty corpus`
- `docs(readme): add Docker Compose deployment note`

## Pull request process

1. Fork + branch off `main`.
2. Write tests for the change (or explain why no test is needed).
3. Keep PRs focused — one logical change per PR. If you caught
   unrelated issues, open a second PR.
4. Update `CHANGELOG.md` **only if the PR is meant to ship in the
   next release** — otherwise leave the ledger alone.
5. Make sure `./dev.sh --setup && pytest packages/*/tests -q`
   passes locally.
6. Open the PR against `main`. CI runs backend tests (Python 3.13),
   modular tests, ruff, bandit, pip-audit, frontend jest, tsc,
   and npm audit. All must pass.

## Regulation entity sources

If you change entity types for a built-in regulation (in the seed or
any recognizer pack), update
[`packages/core/docs/REGULATION_ENTITY_SOURCES.md`](packages/core/docs/REGULATION_ENTITY_SOURCES.md)
with the legal basis (article/section/recital) in the **same PR**.
The CHANGELOG pre-commit hook enforces this pairing.

## Security

Please don't file security issues publicly. Email the maintainer at
the address listed on the [GitHub profile](https://github.com/byerlikaya)
and we'll coordinate a fix + advisory.

## License

Contributions are licensed under the MIT License, the same as the
project. By submitting a pull request you agree that your
contribution can be distributed under those terms.

---

<p align="center">
  <a href="README.md"><strong>🏠 Home</strong></a>
  &nbsp;·&nbsp;
  <a href="docs/FEATURES.md"><strong>✨ Features</strong></a>
  &nbsp;·&nbsp;
  <a href="docs/ARCHITECTURE.md"><strong>🏗️ Architecture</strong></a>
  &nbsp;·&nbsp;
  <a href="docs/DOCUMENT_INGESTION.md"><strong>📊 Document Ingestion</strong></a>
  &nbsp;·&nbsp;
  <a href="docs/SCREENSHOTS.md"><strong>📸 Screenshots</strong></a>
  &nbsp;·&nbsp;
  <strong>🤝 Contributing</strong>
  &nbsp;·&nbsp;
  <a href="CHANGELOG.md"><strong>📝 Changelog</strong></a>
</p>
