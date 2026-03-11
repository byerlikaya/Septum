## Changelog

All notable changes to this project are documented here in a high‑level, date‑based format.

### 2026-03-12

- **KVKK ruleset (6698)**: Expanded entity types to align with Madde 3(d) (kişisel veri: her türlü bilgi) and Madde 6 (özel nitelikli kişisel veriler, 11 kategori). Added: SOCIAL_SECURITY_NUMBER, TAX_ID, COORDINATES, IP_ADDRESS, COOKIE_ID, DEVICE_ID, DNA_PROFILE, MEDICATION, CLINICAL_NOTE, SEXUAL_ORIENTATION, FINANCIAL_ACCOUNT, CREDIT_CARD_NUMBER, BANK_ACCOUNT_NUMBER. REGULATION_ENTITY_SOURCES.md updated with exact article wording, Kurum examples, and full Madde 6 list; recognizer pack docstring updated.
- **Regulation rulesets (GDPR, UK GDPR, CCPA/CPRA, PIPEDA)**: Aligned built-in regulation entity types with official legal texts: GDPR Art. 4(1), Art. 9(1), Recital 30 (added FIRST_NAME, LAST_NAME, ETHNICITY, MAC_ADDRESS, STREET_ADDRESS, CITY, COORDINATES, CLINICAL_NOTE, PASSPORT_NUMBER); UK GDPR same set; CCPA/CPRA § 1798.140 (added ETHNICITY, SOCIAL_SECURITY_NUMBER, FIRST_NAME, LAST_NAME, STREET_ADDRESS, CITY; CPRA added health-related types); PIPEDA s. 2(1) and OPC guidance (added FIRST_NAME, LAST_NAME, STREET_ADDRESS, CITY, MEDICATION). Descriptions and official URLs updated; REGULATION_ENTITY_SOURCES.md updated with exact article/recital wording and PIPEDA subsection.
- **Regulation entity sources doc and rule**: Add backend/docs/REGULATION_ENTITY_SOURCES.md; rule in .cursor requires updating the doc when changing built-in regulation entity types. Seed adds use_ollama_layer to AppSettings defaults.
- **Sanitizer and PII pipeline**: Ollama PII layer wrapped in try/except so failures do not break sanitization; NER uses language-aware confidence threshold (lower for non-English PERSON_NAME/LOCATION); anonymization blocklist adds single-token entities so residual mentions are redacted; NonPiiFilter logic fixed to keep spans when no rule matches; sanitizer_alias_layer prompt made generic (person, location, alias).
- **Chat query sanitization**: User message is now sanitized before retrieval and LLM call (same pipeline as document text).
- **E2E test and ingestion errors**: E2E Turkish PII test no longer skips: uses Presidio-detectable email, language and anon-map mocks, and LLM mock with `on_cloud_failure`; fixture sets `TLDEXTRACT_CACHE` to tmp path. Document upload 500 response now includes `ingestion_error` in detail for easier debugging.
- **NER model overrides**: NER Models settings tab now supports per-language overrides: edit the HuggingFace model ID for any language, restore default per row (persists immediately), and save overrides to the database. Backend stores overrides in `app_settings.ner_model_overrides` and uses them in the sanitizer pipeline and chat.
- **Error logging and Error Log UI**: Centralized error logging for backend and frontend: new `ErrorLog` model and table, `error_logger` service, global exception handler, and `POST /api/error-logs/frontend` for client-reported errors. New Error Logs page under Settings lists, filters, and clears logs with optional stack-trace detail; frontend global error boundary and runtime error listeners report errors to the backend. Sidebar shows error count badge; handled backend errors (e.g. LLM failures, test-llm fallback) are logged; Test LLM returns 200 with `ok: false` when cloud fails and Ollama fallback is used.
- **Document preview**: Copy button shows "Copied" state for user feedback.
- **Chat**: Badge under assistant message when the answer was produced by local Ollama fallback (cloud unavailable).
- **Docs**: README (EN/TR) add Changelog and License links in header.
- **Changelog and rules**: Split changelog entries by commit date (2026-03-11 vs 2026-03-12). Changelog rule updated to require always verifying today’s date (`date +%Y-%m-%d`) and assigning entries to the correct day via `git log --date=short`.

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
