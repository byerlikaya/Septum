![Backend](https://img.shields.io/badge/backend-FastAPI-blue)
![Frontend](https://img.shields.io/badge/frontend-Next.js%2014-black)
![Tests](https://img.shields.io/badge/tests-pytest-informational)
![Coverage](https://img.shields.io/badge/coverage-via%20pytest--cov-lightgrey)
![Focus](https://img.shields.io/badge/focus-Privacy--First-green)
[![Türkçe](https://img.shields.io/badge/lang-TR-red)](README.tr.md)

## Septum — Privacy‑First AI Assistant

Septum is a **privacy‑first middleware and web app** that lets organisations use their **own data** with large language models (LLMs) without exposing raw personal data to the cloud.

In short:
- You upload your documents (PDF, Word, Excel, images, audio, etc.).
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
  - PDFs, Office files, images (OCR), audio recordings (transcripts), emails and more.

- **Approval‑Based Chat**
  - Before anything is sent to the LLM, you see a summary of what will be shared and can approve or reject it.

---

## How It Works (Short Scenario)

1. **Upload your documents**  
   Use the dashboard to upload PDFs, Office files, images or audio files into Septum.

2. **Septum processes and anonymises**  
   - Automatically detects file type, language and personal data inside.  
   - Masks all PII locally and prepares an anonymised representation for search/RAG.

3. **Ask questions**  
   - Example: “What are the termination conditions in this contract?”,  
     “Which products does this customer have?”,  
     “How many recent cases mention X in the last 6 months?”.

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

- **Frontend**: Next.js 14 + React  
  - Provides chat, document management, settings and regulation views.  
  - Communicates with the backend over HTTP and SSE streams.

These details are mainly for developers; end‑users interact with the web UI only.

## Mimari Genel Bakış

Yüksek seviye akış:

1. **Doküman yükleme**
   - Frontend, `POST /api/documents/upload` ile dosya gönderir.
   - Backend:
     1. Dosya tipini **python‑magic** ile tespit eder.
     2. Dil tespiti yapar (lingua + langdetect).
     3. Format’a göre doğru ingester’a yönlendirir (PDF, DOCX, XLSX, Image, Audio, vb.).
     4. Ortaya çıkan düz metni **PolicyComposer + PIISanitizer** pipeline’ından geçirir.
     5. **Anonimleştirilmiş chunk’lar** üretir ve FAISS’e gömer.
     6. Orijinal dosyayı AES‑256‑GCM ile şifreleyerek diske yazar; metadata’yı SQLite’ta saklar.

2. **Chat akışı**
   - Frontend, `/api/chat/ask` endpoint’ine SSE ile mesaj gönderir.
   - Backend:
     1. Kullanıcı sorgusunu aynı sanitizer pipeline’ından geçirir (aktif regülasyonlar + custom rules).
     2. FAISS üzerinden bağlamsal chunk’ları çeker.
     3. **Approval Gate** ile hangi bilgilerin buluta gideceğini kullanıcıya gösterir.
     4. Kullanıcı onay verirse, sadece **placeholder içeren metni** bulut LLM’e yollar.
     5. Gelen cevap yerelde **de‑anonymizer** üzerinden geçirilerek placeholder’lar gerçek değerlere döner.
     6. Sonuç SSE üzerinden frontend’e iletilir.

3. **Ayarlar ve regülasyon yönetimi**
   - Settings ekranlarından:
     - LLM / Ollama / Whisper / OCR ayarları,
     - Varsayılan aktif regülasyonlar,
     - Custom recognizer’lar,
     - NER model map’leri yönetilir.

---

## Backend (FastAPI) Yapısı

Backend kök klasörü: `backend/`

- `app/main.py` — FastAPI uygulama tanımı ve router kayıtları.
- `app/config.py` — Pydantic Settings ile konfigürasyon.
- `app/database.py` — SQLite bağlantısı, `RegulationRuleset` seed işlemleri.
- `app/models/` — SQLAlchemy modelleri:
  - `document.py`, `chunk.py`, `settings.py`, `regulation.py`, `custom_recognizer.py`
- `app/schemas/` — Pydantic şemalar:
  - `document.py`, `chat.py`, `settings.py`, `regulation.py`, `custom_recognizer.py`
- `app/routers/` — FastAPI router’ları:
  - `documents.py`, `chunks.py`, `chat.py`, `approval.py`, `settings.py`, `regulations.py`
- `app/services/`:
  - `ingestion/` — format bazlı ingester’lar (pdf, docx, xlsx, pptx, image, audio, html, markdown, json, yaml, xml, email, epub, rtf).
  - `recognizers/` — regülasyon paketleri (gdpr, hipaa, kvkk, lgpd, ccpa, …) ve `registry.py`.
  - `national_ids/` — ülke‑spesifik ID validator’ları (TCKN, SSN, CPF, Aadhaar, IBAN, vb.).
  - `policy_composer.py` — aktif regülasyon + custom rules birleşimini hazırlar.
  - `language_detector.py` — dil tespiti.
  - `ner_model_registry.py` — dil → model eşlemesi ve lazy loading.
  - `sanitizer.py` — PII tespiti ve placeholder’lama pipeline’ı.
  - `anonymization_map.py` — session‑scoped anonymization haritası + coreference.
  - `document_processor.py`, `vector_store.py`, `llm_router.py`, `deanonymizer.py`, `approval_gate.py`.
- `app/utils/`:
  - `device.py` — CPU/MPS/CUDA seçimi.
  - `crypto.py` — AES‑256‑GCM dosya şifreleme.
  - `text_utils.py` — Unicode NFC + locale‑aware lower (TR, DE vb.).
  - `logger.py` — PII’siz loglama.
- `tests/` — pytest senaryoları (sanitizer, anonymization_map, national_ids, policy_composer, custom_recognizers, document_processor, deanonymizer, llm_router, crypto, ingesters, vb.).

FastAPI tarafında Context7 best‑practice’lerine göre:

- API endpoint’leri **APIRouter** ile modülerleştirilir.
- İstek/yanıt validasyonu için Pydantic modelleri kullanılır.
- DB oturumu, ayarlar ve diğer bağımlılıklar `Depends(...)` ile enjekte edilir.
- Tüm path fonksiyonları async, CPU‑bound işler thread pool’da çalışır.

---

## Frontend (Next.js 14 App Router) Yapısı

Frontend kök klasörü: `frontend/`

- `src/app/`
  - `layout.tsx` — root layout.
  - `page.tsx` — landing / yönlendirme.
  - `chat/page.tsx` — chat ekranı (SSE ile backend’e bağlı).
  - `documents/page.tsx` — doküman listesi ve yükleme.
  - `chunks/page.tsx` — chunk görünümleri.
  - `settings/` — alt sayfalar:
    - `page.tsx` — genel ayarlar.
    - `regulations/page.tsx` — regülasyon yönetimi.
    - `custom-rules/page.tsx` — custom recognizer builder.
- `src/components/`
  - `layout/Sidebar.tsx`, `layout/Header.tsx`
  - `chat/ChatWindow.tsx`, `MessageBubble.tsx`, `ApprovalModal.tsx`, `JsonOutputPanel.tsx`, `DeanonymizationBanner.tsx`
  - `documents/DocumentUploader.tsx`, `DocumentList.tsx`, `DocumentCard.tsx`, `DocumentPreview.tsx`, `TranscriptionPreview.tsx`
  - `chunks/ChunkList.tsx`, `ChunkCard.tsx`, `EntityBadge.tsx`
  - `settings/*` — LLMSettings, PrivacySettings, LocalModelSettings, RAGSettings, IngestionSettings, NERModelSettings, RegulationManager, CustomRuleBuilder
- `src/store/`
  - `chatStore.ts`, `documentStore.ts`, `settingsStore.ts`, `regulationStore.ts`
- `src/lib/`
  - `api.ts` — backend HTTP client.
  - `types.ts` — paylaşılan tipler.

Next.js tarafında Context7 best‑practice’lerine göre:

- App Router yapısı (segment tabanlı routing) kullanılır.
- SSE ve streaming yanıtlar için `EventSource` veya `fetch` + `ReadableStream` kullanılır.
- Tailwind CSS sınıfları `app`, `components` ve ilgili dizinler için taranacak şekilde yapılandırılır.

---

## Teknoloji Yığını

**Backend**
- Python, FastAPI, Uvicorn
- Presidio Analyzer/Anonymizer
- HuggingFace Transformers + sentence‑transformers
- Faiss‑cpu
- lingua‑language‑detector, langdetect
- EasyOCR, OpenCV, Pillow
- Whisper, ffmpeg‑python
- SQLAlchemy + aiosqlite
- cryptography (AES‑256‑GCM)

**Frontend**
- Next.js 14 (App Router)
- React 18
- TypeScript
- Tailwind CSS
- axios, react‑dropzone, lucide‑react

---

## Kurulum

### 1. Ortak gereksinimler

- Python 3.10+
- Node.js 18+ (Next.js 14 için)
- ffmpeg (Whisper için)

### 2. Backend kurulumu

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

`.env` dosyasını `backend/.env.example`’dan kopyalayın:

```bash
cp .env.example .env
```

Gerekli değişkenleri doldurun:

- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` (kullanılacaksa)
- `LLM_PROVIDER` (örn. `anthropic`)
- `USE_OLLAMA`, `OLLAMA_BASE_URL`, `OLLAMA_CHAT_MODEL`, `OLLAMA_DEANON_MODEL`
- `WHISPER_MODEL`
- `ENCRYPTION_KEY` (32 byte base64 veya hex; boş bırakılırsa ilk çalıştırmada otomatik üretim mantığına göre)
- `DB_PATH`, `LOG_LEVEL`, `DEFAULT_ACTIVE_REGULATIONS`, vb.

Ardından backend’i çalıştırın:

```bash
uvicorn app.main:app --reload
```

### 3. Frontend kurulumu

```bash
cd frontend
npm install
npm run dev
```

Varsayılan olarak:
- Backend: `http://localhost:8000`
- Frontend: `http://localhost:3000`

Frontend `src/lib/api.ts` içinde backend base URL doğrulanmalıdır.

---

## Test Çalıştırma

Backend testleri için proje içinde özel bir kural seti bulunur (`/test` kuralı):

- Değişen dosyaya göre ilgili pytest dosyası çalıştırılır. Örnek:
  - `sanitizer.py` → `tests/test_sanitizer.py`
  - `anonymization_map.py` → `tests/test_anonymization_map.py`
  - `app/services/national_ids/*` → `tests/test_national_ids.py`
  - `app/services/ingestion/*` → `tests/test_ingesters.py`
  - vb.
- Eşleşme yoksa tüm test suite’i çalıştırılır.

Elle çalıştırmak için:

```bash
cd backend
pytest tests/ -v
```

Cloud LLM’e gerçek istek atan testler **mock edilmelidir**; gerçek API çağrısı yapan testler bug kabul edilir.

---

## Güvenlik ve Gizlilik (Önemli Noktalar)

- Ham PII asla log’lanmaz ve buluta gönderilmez.
- Anonymization map (maskeler → gerçek değerler) yalnızca bellek içinde tutulur, diske yazılmaz.
- Dosya tipleri uzantıya göre değil, içerik imzasına göre tespit edilir.
- Dosyalar diskte AES‑256‑GCM ile şifreli saklanır; çözme işlemi sadece önizleme sırasında ve bellek içinde yapılır.
- Birden fazla regülasyon aynı anda aktifken, her zaman **en kısıtlayıcı maskeleme** politikası uygulanır.

---

## Yol Haritası / Genişletme

- Yeni ülke regülasyonları için `/new-regulation` kural seti ile yeni pack eklenebilir.
- Yeni ulusal kimlik formatları için `/new-recognizer` ile validator + recognizer eklenebilir.
- Yeni doküman formatları için `/new-ingester` ile yeni ingester implementasyonu eklenebilir.
- NER model haritası Settings → NER Models üzerinden kullanıcı tarafından güncellenebilir.

