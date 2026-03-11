## Changelog

All notable changes to this project are documented here in a high‑level, date‑based format.

### 2026-03-11

- **OCR and PII improvements**: Enhanced image/PDF OCR quality, improved OCR ingestion flow, and refined person name masking and PII handling.
- **Spreadsheet enhancements**: Added spreadsheet schema metadata, numeric-aware chat for tabular content, and limited schema display to truly tabular documents.
- **Infrastructure and tooling cleanup**: Unified environment loading defaults (including Ollama), and removed legacy coverage/Codecov tooling.
- **ODS support**: Added ODS (OpenDocument Spreadsheet) ingestion support and documented it in both English and Turkish READMEs.
- **LLM routing and prompt catalog**: Refactored the LLM router into a provider-strategy layer, introduced a document processing pipeline orchestrator, centralized all backend LLM/Ollama prompts under `PromptCatalog`, and added a shared AppSettings factory plus updated tests.

### 2026-03-10

- **Documentation and licensing**: Expanded README content with PII pipeline and AI gateway sections, screenshot gallery, and clarified extension workflow; added MIT license and kept EN/TR READMEs in sync.
- **Testing and quality**: Improved backend and frontend coverage, added Jest setup, fixed async engine and aiosqlite warnings, and ensured backend tests import the app package correctly.
- **Sanitization and PII pipeline**: Hardened sanitizer structure and robustness, generalized the PII pipeline, added configurable text normalization rules and non‑PII filters, and localized deanonymization banner copy.
- **Chat experience**: Added global i18n for chat UI, approval flow localization, chat debug tools, document‑optional chats, generic prompts, and post‑processing for malformed LLM output.
- **Platform and tooling**: Introduced Dockerfiles and docker‑compose for backend/frontend, tracked env templates, pinned backend dependencies, and updated docs and dependencies.
- **UI and layout**: Refined documents, chunks, and settings UIs; improved sidebar layout; and added the Septum logo across the app.

### 2026-03-09

- **Core platform foundation**: Bootstrapped the Septum project skeleton with core utils, crypto, database models, and health checks.
- **Ingestion pipeline**: Implemented ingestion base and office ingesters (documents, spreadsheets, presentations), plus image and audio ingesters with health checks.
- **Privacy and recognition engine**: Added national ID validators and tests, a multilayer sanitizer, anonymization map with coreference handling, and a regulation‑aware recognizer registry and policy composer.
- **Vector store and retrieval**: Introduced an encrypted FAISS vector store per document and ignored local index artifacts from version control.
- **Backend services and frontend shell**: Added LLM router, deanonymizer, approval gate, chat pipeline wiring, settings sync, settings UI, regulations UI, documents UI, and the initial Next.js frontend shell with layout and API client.
