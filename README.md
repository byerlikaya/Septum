<p align="center">
  <img src="septum_logo.png" alt="Septum logo" width="220" />
</p>

<p align="center">
  <img src="https://img.shields.io/badge/backend-FastAPI-blue" alt="Backend: FastAPI" />
  <img src="https://img.shields.io/badge/frontend-Next.js%2016-black" alt="Frontend: Next.js 16" />
  <img src="https://img.shields.io/badge/tests-pytest-informational" alt="Tests: pytest" />
  <img src="https://img.shields.io/badge/focus-Privacy--First-green" alt="Focus: Privacy-First" />
  <a href="README.tr.md">
    <img src="https://img.shields.io/badge/lang-TR-red" alt="Turkish README" />
  </a>
  <br />
  <img src="https://img.shields.io/badge/security_scan-passing_(2026--03--10)-brightgreen" alt="Security Scan: passing (2026-03-10)" />
  <img src="https://img.shields.io/badge/deps-audit_clean-brightgreen" alt="Dependencies: audit clean" />
</p>

<p align="center">
  <a href="#screenshots"><strong>View screenshots</strong></a>
  ·
  <a href="CHANGELOG.md"><strong>Changelog</strong></a>
  ·
  <a href="LICENSE"><strong>License</strong></a>
</p>

## Septum — Privacy‑First AI Assistant

Septum is a **privacy‑first middleware and web app** that lets organisations use their **own data** with large language models (LLMs) without exposing raw personal data to the cloud.

In short:
- You upload your documents (PDF, Word, Excel, OpenDocument spreadsheets (.ods), images, audio, etc.).
- Septum **locally detects and anonymises** personal data (PII) in them.
- Your questions are answered using this anonymised view of your data.
- The answer is then **re‑mapped locally** so you see real names and values again.

No raw, directly identifying personal data ever leaves your machine.

---

## What Problems Does It Solve?

- **Safe enterprise document Q&A**  
  - Query sensitive content such as policies, contracts, customer files, health or HR records with an LLM.  
  - The LLM only ever sees masked placeholders (e.g. `[PERSON_1]`, `[EMAIL_2]`), not real identities.

- **Regulation‑friendly data sharing**  
  - Helps reduce GDPR / KVKK / HIPAA compliance risk by anonymising data **before** anything touches the cloud.

- **Internal knowledge assistant**  
  - Indexes your own documents into a vector store (RAG) so you can build powerful internal search and Q&A over company knowledge.

In one sentence: Septum is a **safety layer** for teams who want LLM power **without leaking personal data**.

---

## Where Can It Be Used?

- **Finance**  
  Search and summarise customer contracts, credit files, internal procedures, while keeping PII protected.

- **Healthcare**  
  Use anonymised patient files, reports and lab results in clinical support tools without exposing raw health data.

- **Legal & Compliance**  
  Explore contracts, case files, GDPR/KVKK docs without sending names, IDs or addresses to the cloud.

- **HR & Operations**  
  Build internal assistants over personnel files, reviews and salary information without leaking sensitive details.

The common theme: **leverage LLMs while keeping personal data encrypted and on‑premise**.

---

## Key Features

- **Local PII Protection**
  - Raw personal data (names, IDs, addresses, emails, etc.) never leaves your machine.
  - Documents are stored encrypted at rest; decryption only happens in memory when needed for display.

- **Multi‑Regulation Support**
  - Built‑in packs for GDPR, KVKK, CCPA, HIPAA, LGPD and more.
  - Multiple regulations can be active at once; Septum applies the **most restrictive** masking policy.

- **Custom Rules**
  - Define your own patterns: “mask everything matching this regex”, “whenever these keywords appear, treat them as sensitive”, “catch any salary‑related expressions”, etc.

- **Rich Document Format Support**
  - PDFs, Office and spreadsheet files (e.g. XLSX, ODS), images (OCR), audio recordings (transcripts), emails and more.

- **Approval‑Based Chat**
  - Before anything is sent to the LLM, you see a summary of what will be shared and can approve or reject it.

- **Desktop Assistant Mode (ChatGPT / Claude)**
  - Optional mode that sends your question (or a RAG-enabled prompt with document context) directly to a locally installed desktop assistant client (for example the official ChatGPT or Claude desktop apps) instead of the cloud LLM behind Septum.
  - When "Use document context (RAG)" is enabled, Septum retrieves and sanitizes relevant chunks from your uploaded documents and constructs a RAG prompt using the same logic as Cloud Mode, then sends this full prompt to the desktop assistant via OS‑level automation.
  - Uses OS‑level automation on your own machine (window focus, clipboard, keystrokes); no additional cloud calls are made beyond what the desktop client already performs.
  - Fully opt‑in via Settings and disabled by default; when enabled, you can switch between Cloud Mode and Desktop Assistant Mode from the chat screen and choose which desktop client to target.

- **Professional Hybrid Retrieval**
  - Combines BM25 (keyword matching) with FAISS (semantic similarity) using Reciprocal Rank Fusion (RRF).
  - Delivers superior retrieval quality for legal/contract queries by blending exact term matching with semantic understanding.
  - Configurable weights (alpha/beta) for fine-tuning retrieval balance.
  - Adaptive context size and document-theme retrieval improve answers for holistic questions (e.g. “interpret this document”, “summarise the report”) without requiring targeted phrasing.

- **Structured Data Extraction**
  - Automatically detects and extracts tables and key-value pairs from PDF documents (e.g., "Employee Title: Engineer").
  - Creates separate field chunks with metadata for better retrieval of structured contract information.
  - Uses pdfplumber for precise table detection and field extraction.

- **Enhanced Semantic Chunking**
  - Intelligent document splitting that preserves structure while respecting semantic coherence.
  - Hybrid approach: structural phase (numbered sections) + semantic phase (embedding-based similarity).
  - Uses LangChain's SemanticChunker with gradient threshold for optimal chunk boundaries.
  - Prevents arbitrary splits mid-clause, improving LLM context quality.

---

## How It Works (Short Scenario)

1. **Upload your documents**  
   Use the Documents page or the upload area in the Chat sidebar to add PDFs, Office files, images or audio files into Septum.

2. **Septum processes and anonymises**  
   - Automatically detects file type, language and personal data inside.  
   - Masks all PII locally and prepares an anonymised representation for search/RAG.

3. **Ask questions**  
   - Example: “What are the termination conditions in this contract?”,  
     “Which products does this customer have?”,  
     “How many recent cases mention X in the last 6 months?”.  
   - When you upload a document from the chat screen, that document is selected by default so your questions are scoped to it immediately.

4. **Approve before sending**  
   - Septum shows you what anonymised content is about to be sent to the LLM.  
   - Only after your approval is the masked text sent.

5. **See the answer with real values**  
   - When the LLM responds, Septum locally replaces placeholders with the original values so you see a natural, human‑readable answer.

---

## Short Technical Overview

- **Backend**: Python + FastAPI  
  - Handles document processing, anonymisation, encryption and LLM integration.  
  - All PII handling happens on the server side you control.

- **Frontend**: Next.js 16 + React 19  
  - Provides chat, document management, settings and regulation views.  
  - Communicates with the backend over HTTP and SSE streams.

These details are mainly for developers; end‑users interact with the web UI only.

## High‑Level Architecture

High‑level flow:

1. **Document upload**
   - The frontend sends files via `POST /api/documents/upload`.
   - The backend:
     1. Detects the file type using **python‑magic**.  
     2. Detects the language (lingua + langdetect).  
     3. Routes to the appropriate ingester for the format (PDF, DOCX, XLSX, ODS, image, audio, etc.).  
     4. Sends the extracted plain text through the **PolicyComposer + PIISanitizer** pipeline.  
     5. Produces **anonymised chunks** and embeds them into FAISS.  
     6. Encrypts the original file with AES‑256‑GCM on disk and stores metadata in SQLite.

2. **Chat flow**
   - The frontend sends messages to `/api/chat/ask` using SSE.
   - The backend:
     1. Sanitises the user query with the same pipeline (active regulations + custom rules).  
     2. Retrieves contextual chunks from FAISS.  
     3. Uses the **Approval Gate** to show which information will be sent to the cloud.  
     4. If the user approves, sends only **placeholder‑masked text** to the cloud LLM.  
     5. Runs the response through the local **de‑anonymiser** so placeholders are mapped back to real values.  
     6. Streams the final result to the frontend via SSE.

3. **Settings and regulation management**
   - From the Settings screens you can manage:
     - LLM / Ollama / Whisper / OCR options  
     - Default active regulations  
     - Custom recognisers  
     - NER model mappings

---

## PII Detection & Anonymisation Pipeline

Septum’s core is a **multi‑layer PII detection pipeline** that combines regulation‑aware recognisers, language‑specific NER models and country‑specific validators under the active policies.

At a high level:

1. **Policy composition**
   - Active regulation rulesets (e.g. GDPR, KVKK, HIPAA, CCPA, LGPD, etc.) are merged into a single **composed policy** via the `PolicyComposer`.
   - The composed policy contains:
     - The union of all entity types that must be protected.
     - A list of recognisers (built‑in + custom) that should run for the current configuration.
   - Custom recognisers (regex, keyword list, LLM‑prompt based) are also injected into this policy.

2. **Layer 1 — Presidio recognisers**
   - Septum uses **Microsoft Presidio** as the first line of detection, with recogniser packs organised by regulation.
   - Each regulation pack contributes recognisers for:
     - Identity (names, national IDs, passports, etc.)
     - Contact details (emails, phones, addresses, IPs, URLs, social handles)
     - Financial identifiers (credit cards, bank accounts, IBAN/SWIFT, tax IDs)
     - Health, demographic and organisational attributes
   - Users can extend this layer with **custom recognisers** (regex patterns, keyword lists, or LLM‑prompt based rules).
   - National IDs and financial identifiers use **country‑specific checksum validators** to reduce false positives.
   - Only recognisers that are relevant for the active regulations are loaded into the Presidio registry.

3. **Layer 2 — Language‑specific NER**
   - For each document and query, Septum detects the language and loads a **language‑appropriate HuggingFace NER model**, with a multilingual fallback when needed.
   - The NER layer:
     - Complements Presidio by catching entities that are context‑dependent or language‑specific.
     - Uses state‑of‑the‑art XLM‑RoBERTa based models (e.g. `Davlan/xlm-roberta-base-wikiann-ner` for 20 languages, `akdeniz27/xlm-roberta-base-turkish-ner` for Turkish).
     - Runs device‑aware (CUDA → MPS → CPU) and uses cached pipelines for efficiency.
   - This layer is configurable per language via the **NER Model Settings** screen.

4. **Layer 3 — Ollama context‑aware layer**
   - When enabled (`use_ollama_layer=True`), Septum uses a **local Ollama LLM** to detect context‑dependent PII that the first two layers may miss:
     - Nicknames, aliases, and informal mentions (e.g. "john" when "John Doe" was detected earlier).
     - Family member names in context (e.g. "father's name: ahmed", "mother: sarah").
     - Codenames, pet names, and organisation‑specific labels.
   - This layer preserves exact casing and runs entirely on‑device, ensuring no PII leaves the local machine.
   - It is disabled for numeric‑heavy structured content (e.g. price lists, invoices) to avoid noisy detections.

5. **Anonymisation & coreference**
   - All spans from the above layers are merged, deduplicated and fed into the `AnonymizationMap`:
     - Each unique entity is replaced with a stable placeholder (e.g. `[PERSON_1]`, `[EMAIL_2]`).
     - Coreference handling ensures that repeated mentions (e.g. full name → first name) are mapped to the **same** placeholder.
     - A configurable blocklist can enforce extra replacements beyond detected entities.
   - The anonymisation map never leaves memory and is never written to disk.

6. **Multi‑regulation conflict handling**
   - When multiple regulations are active at the same time, Septum always applies the **most restrictive** masking behaviour:
     - If any regulation considers a value PII, it is treated as PII.
     - Overlapping entities are merged into a single placeholder while retaining metadata about which regulations required masking.

In practice, this means Septum does not rely on a single heuristic: it combines regulation packs, NER, custom rules and algorithmic validators into one consistent anonymisation step before anything can leave your environment.

---

## Septum as an AI Privacy Gateway

Beyond the web UI, Septum can act as an **HTTP gateway in front of any LLM‑powered application**. Instead of calling a cloud LLM directly, your app can call Septum, which:

1. Sanitises the request (masking PII according to active regulations and custom rules).
2. Retrieves anonymised context from the vector store when RAG is enabled.
3. Forwards only **masked text** to the configured LLM provider.
4. De‑anonymises the response locally before returning it to the caller.

Conceptually:

Your app → **Septum (sanitise + RAG + approval)** → Cloud LLM  
Your data and raw PII never leave your environment.

A simplified example flow:

1. **Your app** sends a chat request:

   ```json
   POST /api/chat/ask
   {
     "messages": [
       { "role": "user", "content": "Summarise the last 3 contracts for Ahmet Yılmaz at ACME Corp." }
     ],
     "document_ids": [123, 124, 125],
     "metadata": {
       "regulations": ["gdpr", "kvkk"],
       "require_approval": true
     }
   }
   ```

2. **Septum**:
   - Detects language and relevant PII in the query and related documents.
   - Replaces identifiers with placeholders (e.g. `[PERSON_1]`, `[ORG_1]`).
   - Retrieves anonymised chunks from the vector store (RAG).
   - Optionally shows an **approval view** of what will be sent upstream.
   - Calls the configured LLM provider with masked context only.

3. **Cloud LLM** responds with an answer that only contains placeholders.

4. **Septum**:
   - Uses the in‑memory anonymisation map to replace placeholders back to original values.
   - Streams the final, human‑readable answer back to your app over HTTP/SSE.

In this mode, Septum behaves as a **drop‑in privacy layer**:

- Existing tools keep their own UI and logic.
- You centralise PII handling, regulation rules and auditability in one place.
- You can switch or mix LLM providers behind Septum without changing how your app handles personal data.

---

## Backend (FastAPI) Structure

Backend root: `backend/`

- `app/main.py` — FastAPI application definition and router registration  
- `app/config.py` — configuration via Pydantic Settings  
- `app/database.py` — SQLite connection and `RegulationRuleset` seeding  
- `app/models/` — SQLAlchemy models:  
  - `document.py`, `chunk.py`, `settings.py`, `regulation.py`, `custom_recognizer.py`  
- `app/schemas/` — Pydantic schemas:  
  - `document.py`, `chat.py`, `settings.py`, `regulation.py`, `custom_recognizer.py`  
- `app/routers/` — FastAPI routers:  
  - `documents.py`, `chunks.py`, `chat.py`, `approval.py`, `settings.py`, `regulations.py`  
- `app/services/`:  
  - `ingestion/` — format‑specific ingesters (PDF, DOCX, XLSX, ODS, PPTX, image, audio, HTML, markdown, JSON, YAML, XML, email, EPUB, RTF)  
  - `recognizers/` — regulation packs (gdpr, hipaa, kvkk, lgpd, ccpa, …) and `registry.py`  
  - `national_ids/` — country‑specific ID validators (TCKN, SSN, CPF, Aadhaar, IBAN, etc.)  
  - `policy_composer.py` — composes active regulations and custom rules into a single policy  
  - `language_detector.py` — language detection  
  - `ner_model_registry.py` — language → model mapping and lazy loading  
  - `sanitizer.py` — PII detection and placeholder pipeline  
  - `anonymization_map.py` — session‑scoped anonymisation map + coreference handling  
  - `document_processor.py`, `vector_store.py`, `llm_router.py`, `deanonymizer.py`, `approval_gate.py`  
- `app/utils/`:  
  - `device.py` — CPU/MPS/CUDA selection  
  - `crypto.py` — AES‑256‑GCM file encryption  
  - `text_utils.py` — Unicode NFC + locale‑aware lowercasing  
  - `logger.py` — logging without raw PII  
- `tests/` — pytest scenarios (sanitizer, anonymization_map, national_ids, policy_composer, custom_recognizers, document_processor, deanonymizer, llm_router, crypto, ingesters, etc.).

With FastAPI we follow Context7 best practices:

- API endpoints are modularised with **APIRouter**.  
- Request/response validation uses Pydantic models.  
- DB session, settings and other dependencies are injected via `Depends(...)`.  
- All path functions are async; CPU‑bound tasks run in a thread pool.

---

## Frontend (Next.js App Router) Structure

Frontend root: `frontend/`

- `src/app/`  
  - `layout.tsx` — root layout  
  - `page.tsx` — landing / redirect  
  - `chat/page.tsx` — chat screen (connected to backend via SSE)  
  - `documents/page.tsx` — document list and upload  
  - `chunks/page.tsx` — chunk views  
  - `settings/` — sub‑pages:  
    - `page.tsx` — general settings  
    - `regulations/page.tsx` — regulation management  
    - `custom-rules/page.tsx` — custom recogniser builder  
- `src/components/`  
  - `layout/Sidebar.tsx`, `layout/Header.tsx`  
  - `chat/ChatWindow.tsx`, `MessageBubble.tsx`, `ApprovalModal.tsx`, `JsonOutputPanel.tsx`, `DeanonymizationBanner.tsx`  
  - `documents/DocumentUploader.tsx`, `DocumentList.tsx`, `DocumentCard.tsx`, `DocumentPreview.tsx`, `TranscriptionPreview.tsx`  
  - `chunks/ChunkList.tsx`, `ChunkCard.tsx`, `EntityBadge.tsx`  
  - `settings/*` — `LLMSettings`, `PrivacySettings`, `LocalModelSettings`, `RAGSettings`, `IngestionSettings`, `NERModelSettings`, `RegulationManager`, `CustomRuleBuilder`  
- `src/store/`  
  - `chatStore.ts`, `documentStore.ts`, `settingsStore.ts`, `regulationStore.ts`  
- `src/lib/`  
  - `api.ts` — backend HTTP client  
  - `types.ts` — shared types

On the Next.js side we follow Context7 best practices:

- Uses the App Router (segment‑based routing).  
- SSE and streaming responses use `EventSource` or `fetch` + `ReadableStream`.  
- Tailwind CSS is configured to scan `app`, `components` and related directories.

---

## Technology Stack

**Backend**
- Python, FastAPI, Uvicorn  
- Presidio Analyzer/Anonymizer  
- HuggingFace Transformers + sentence‑transformers  
- faiss‑cpu  
- lingua‑language‑detector, langdetect  
- EasyOCR, OpenCV, Pillow  
- Whisper, ffmpeg‑python  
- SQLAlchemy + aiosqlite  
- cryptography (AES‑256‑GCM)

**Frontend**
- Next.js 16 (App Router)  
- React 19  
- TypeScript  
- Tailwind CSS  
- axios, react‑dropzone, lucide‑react

---

## Setup

### 1. Shared prerequisites

- Python 3.10+  
- Node.js 18+ (for Next.js 16)  
- ffmpeg (for Whisper)

### 2. Backend setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

Create your `.env` from `backend/.env.example`:

```bash
cp .env.example .env
```

Fill in the required variables:

- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` (if used)  
- `LLM_PROVIDER` (e.g. `anthropic`)  
- `USE_OLLAMA`, `OLLAMA_BASE_URL`, `OLLAMA_CHAT_MODEL`, `OLLAMA_DEANON_MODEL`  
- `WHISPER_MODEL`  
- `ENCRYPTION_KEY` (32‑byte base64 or hex; if left empty, the app will auto‑generate it on first run according to its key‑management logic)  
- `DB_PATH`, `LOG_LEVEL`, `DEFAULT_ACTIVE_REGULATIONS`, etc.

Then start the backend:

```bash
uvicorn app.main:app --reload
```

### 3. Frontend setup

```bash
cd frontend
npm install
npm run dev
```

By default:
- Backend: `http://localhost:8000`  
- Frontend: `http://localhost:3000`

Ensure the backend base URL in `src/lib/api.ts` matches your environment.

---

## Running Tests

The project includes a custom `/test` rule inside Septum:

- Based on the changed file, the corresponding pytest file is executed. Examples:  
  - `sanitizer.py` → `tests/test_sanitizer.py`  
  - `anonymization_map.py` → `tests/test_anonymization_map.py`  
  - `app/services/national_ids/*` → `tests/test_national_ids.py`  
  - `app/services/ingestion/*` → `tests/test_ingesters.py`  
  - etc.  
- If no match is found, the full test suite is executed.

To run tests manually:

```bash
cd backend
pytest tests/ -v
```

Any tests that would send real requests to a cloud LLM **must be mocked**; tests that hit real external LLM APIs are treated as bugs.

---

## Security & Privacy Highlights

- Raw PII is never logged and never sent to the cloud.  
- The anonymisation map (placeholders → real values) is kept only in memory and never written to disk.  
- File types are detected by content signature, not by extension.  
- Files are stored encrypted on disk with AES‑256‑GCM; decryption happens only in memory during preview.  
- When multiple regulations are active at the same time, Septum always applies the **most restrictive** masking policy.

---

## Roadmap & Extensibility

- Add new country regulations by creating new regulation packs in the recogniser registry.  
- Add new national ID formats by adding validators and recognisers in the national ID module.  
- Add new document formats by implementing dedicated ingesters in the ingestion layer.  
- Update NER model mappings from the Settings → NER Models screen.

---

## Screenshots

**1. Chat experience**

<p align="center">
  <img src="screenshots/1-chat.png" alt="Chat screen with approval-based sharing" width="900" />
</p>

**2. Documents overview**

<p align="center">
  <img src="screenshots/2-documents.png" alt="Documents list and upload view" width="900" />
</p>

**3. Chunks and entities**

<p align="center">
  <img src="screenshots/3-chunks.png" alt="Chunks view with detected entities" width="900" />
</p>

**4. Cloud LLM settings**

<p align="center">
  <img src="screenshots/4-cloudllm.png" alt="Cloud LLM configuration settings" width="900" />
</p>

**5. Privacy & sanitisation layers**

<p align="center">
  <img src="screenshots/5-privacySanitization.png" alt="Privacy and sanitisation settings" width="900" />
</p>

**6. Local model configuration**

<p align="center">
  <img src="screenshots/6-localmodels.png" alt="Local model settings" width="900" />
</p>

**7. RAG configuration**

<p align="center">
  <img src="screenshots/7-rag.png" alt="RAG configuration settings" width="900" />
</p>

**8. Ingestion pipeline settings**

<p align="center">
  <img src="screenshots/8-ingestion.png" alt="Ingestion and OCR/transcription settings" width="900" />
</p>

**9. Text normalisation rules**

<p align="center">
  <img src="screenshots/9-textNormalizationRules.png" alt="Text normalisation rule configuration" width="900" />
</p>

**10. NER model mappings**

<p align="center">
  <img src="screenshots/10-NERModels.png" alt="Language to NER model mapping settings" width="900" />
</p>

**11. Regulation manager**

<p align="center">
  <img src="screenshots/11-regulations.png" alt="Regulation ruleset management view" width="900" />
</p>

