<p align="center">
  <img src="assets/septum_logo.svg" alt="Septum logo" width="320" />
</p>

<h3 align="center">Your data never leaves. Your AI still works.</h3>

https://github.com/user-attachments/assets/1a033953-cd04-41d5-9b80-72bd26984490

<p align="center">
  <a href="https://github.com/byerlikaya/Septum/actions/workflows/tests.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/byerlikaya/Septum/tests.yml?branch=main&style=for-the-badge&logo=githubactions&logoColor=white&label=tests&color=43A047" alt="CI Tests" />
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/github/license/byerlikaya/Septum?style=for-the-badge&logo=opensourceinitiative&logoColor=white&label=license&color=607D8B" alt="License: MIT" />
  </a>
  <a href="README.tr.md">
    <img src="https://img.shields.io/badge/lang-Türkçe-E53935?style=for-the-badge" alt="Turkish README" />
  </a>
  <a href="https://github.com/crewforth/crewforth">
    <img src="https://img.shields.io/badge/built%20with-Crewforth-37474F?style=for-the-badge" alt="Built with Crewforth" />
  </a>
</p>

<p align="center">
  <a href="https://byerlikaya.github.io/Septum/"><strong>🏠 Documentation</strong></a>
  &nbsp;·&nbsp;
  <a href="https://byerlikaya.github.io/Septum/installation"><strong>🚀 Installation</strong></a>
  &nbsp;·&nbsp;
  <a href="https://byerlikaya.github.io/Septum/features"><strong>✨ Features</strong></a>
  &nbsp;·&nbsp;
  <a href="https://byerlikaya.github.io/Septum/architecture"><strong>🏗️ Architecture</strong></a>
</p>

---

## What is Septum?

Septum is a privacy-first AI middleware. You upload your own documents and ask questions about them, or chat, with ChatGPT, Claude, Gemini, or any other cloud LLM. Before anything leaves your machine, Septum detects personal information and replaces it with placeholders.

Cloud LLMs are useful for company knowledge, but that knowledge is full of names, IDs, phone numbers and addresses. Septum keeps those values out of the cloud request, then restores the real values in the answer you read. The cloud model does the work without seeing the personal data.

Septum applies several privacy regulations at once (17 built-in packs; the most restrictive rule wins) and keeps an audit trail that contains no raw personal data.

## How it works

1. **Mask locally.** Documents and your typed questions pass through local detection. Each personal value becomes a placeholder such as `[PERSON_NAME_1]`.
2. **Review.** With the approval gate on, you see the masked question, the retrieved excerpts and the exact request before it is sent.
3. **Ask the cloud LLM.** Only the masked text is sent.
4. **Restore locally.** Placeholders in the answer are replaced with the real values on your machine.

```
Document chunk:  Elif Demir (TCKN 10000000146) has been discharged.
Sent to cloud:   [PERSON_NAME_1] (TCKN [NATIONAL_ID_1]) has been discharged.
```

## Quick start

```bash
git clone https://github.com/byerlikaya/Septum.git && cd Septum
docker compose -f docker-compose.standalone.yml up --build
```

Open **http://localhost:3000**. The setup wizard first asks for a setup token, which is printed to the container log (`docker compose -f docker-compose.standalone.yml logs septum`). It then configures the database, cache, LLM provider, regulations and the admin account. For the full stack, the air-gapped and internet-facing splits, and the source install, see the [Installation guide](https://byerlikaya.github.io/Septum/installation).

## Documentation

- [Installation](https://byerlikaya.github.io/Septum/installation): topologies, setup wizard, upgrades, troubleshooting
- [Features](https://byerlikaya.github.io/Septum/features): detection pipeline, regulation packs, MCP server, REST API
- [Architecture](https://byerlikaya.github.io/Septum/architecture): modules, security zones, model integrity, audit trail
- [Workflows](https://byerlikaya.github.io/Septum/workflows) and [Use cases](https://byerlikaya.github.io/Septum/use-cases)
- [Benchmark](https://byerlikaya.github.io/Septum/benchmark): detection results per regulation and data source
- [Changelog](https://byerlikaya.github.io/Septum/changelog)
- [Contributing](CONTRIBUTING.md)

## Support and license

Septum is open source under the MIT license; see [LICENSE](LICENSE). If it helps your team, star the repository on [GitHub](https://github.com/byerlikaya/Septum) and open an issue for any bug or missing feature you need.
