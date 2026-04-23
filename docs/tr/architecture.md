---
title: "Mimari ve Teknik Referans"
description: "Yedi modüllü yerleşim, güvenlik bölgeleri, dağıtım topolojileri, API referansı."
---

# Septum — Mimari ve Teknik Referans

<p align="center">
  <a href="../readme.md"><strong>🏠 Ana Sayfa</strong></a>
  &nbsp;·&nbsp;
  <a href="installation.md"><strong>🚀 Kurulum</strong></a>
  &nbsp;·&nbsp;
  <a href="benchmark.md"><strong>📈 Benchmark</strong></a>
  &nbsp;·&nbsp;
  <a href="features.md"><strong>✨ Özellikler</strong></a>
  &nbsp;·&nbsp;
  <strong>🏗️ Mimari</strong>
  &nbsp;·&nbsp;
  <a href="document-ingestion.md"><strong>📊 Doküman İşleme</strong></a>
  &nbsp;·&nbsp;
  <a href="screenshots.md"><strong>📸 Ekran Görüntüleri</strong></a>
</p>

---

## İçindekiler

- [Mimari Genel Bakış](#mimari-genel-bakış)
- [Septum'u Bir AI Gizlilik Geçidi Olarak Kullanmak](#septumu-bir-ai-gizlilik-geçidi-olarak-kullanmak)
- [Modüler Paket Yerleşimi](#modüler-paket-yerleşimi)
- [Paket İçerikleri](#paket-içerikleri)
- [Frontend (Next.js App Router) Yapısı](#frontend-nextjs-app-router-yapısı)
- [Teknoloji Yığını](#teknoloji-yığını)
- [Güvenlik ve Gizlilik](#güvenlik-ve-gizlilik)
- [Denetim Kaydı ve Uyumluluk Raporlama](#denetim-kaydı-ve-uyumluluk-raporlama)
- [LLM Dayanıklılığı ve İzlenebilirlik](#llm-dayanıklılığı-ve-i̇zlenebilirlik)
- [API Referansı](#api-referansı)

---

## Mimari Genel Bakış

Septum, `packages/` dizini altında yer alan **bağımsız olarak kurulabilir yedi pakete** bölünmüştür; bu paketler üç güvenlik bölgesine dağıtılır.

- **Air-gapped bölge** (`septum-core`, `septum-mcp`, `septum-api`, `septum-web`) — tüm PII işlemleri burada yürür. Bu paketlerin internete çıkan tek bir bağımlılığı yoktur; `septum-core` ayrıca `httpx` / `requests` / `urllib` import'larını kod seviyesinde yasaklar, böylece ham PII kazara dışarı sızamaz.
- **Köprü** (`septum-queue`) — bölgeler arasında yalnızca önceden maskelenmiş payload'lar taşır. İki backend seçeneği vardır: dosya (air-gapped varsayılanı) ve Redis Streams (`[redis]` extra'sı). Ham PII köprüden geçemez; bu kısıt kod sözleşmesiyle garanti altındadır.
- **Internet-facing bölge** (`septum-gateway`, `septum-audit`) — maskelenmiş LLM isteklerini Anthropic / OpenAI / OpenRouter'a iletir ve PII içermeyen uyumluluk telemetrisi yazar. Kod sözleşmesi gereği bu paketler `septum-core`'u asla import etmez; Dockerfile yerleşimi de `packages/core/` dizinini gateway ve audit imajlarına kopyalamaz — kısıtlama imaj katmanında da zorunlu kılınır.

| Bölge | Paket | Rol |
|:---|:---|:---|
| Air-gapped | `septum-core` | PII tespit, maskeleme, geri yazma, regülasyon motoru. Ağ bağımlılığı yoktur. |
| Air-gapped | `septum-mcp` | Claude Code / Desktop / Cursor'a stdio üzerinden core araçlarını açan MCP sunucusu. |
| Air-gapped | `septum-api` | FastAPI REST uç noktaları, doküman pipeline'ı, kimlik doğrulama, hız sınırlama. |
| Air-gapped | `septum-web` | Next.js 16 panel (App Router + React 19). |
| Köprü | `septum-queue` | Soyut `QueueBackend` Protocol + envelope dataclass'ları; dosya / Redis Streams somut backend'leri. |
| Internet-facing | `septum-gateway` | Bulut LLM yönlendiricisi. Kuyruktan maskelenmiş istekleri tüketir, maskelenmiş cevapları geri yayınlar. |
| Internet-facing | `septum-audit` | Append-only JSONL sink + JSON / CSV / Splunk HEC exporter'ları. Opsiyonel kuyruk tüketicisi. |

Genel akış:

1. **Doküman yükleme**
   - Frontend, `POST /api/documents/upload` uç noktasına dosyayı gönderir.
   - Backend sırasıyla:
     1. Dosya tipini **python-magic** ile tespit eder.
     2. Dili tespit eder (lingua + langdetect).
     3. Formata uygun ingester'a yönlendirir (PDF, DOCX, XLSX, ODS, görsel, ses vb.).
     4. Çıkarılan düz metni **PolicyComposer + PIISanitizer** hattından geçirir.
     5. **Anonimleştirilmiş chunk'lar** üretir ve FAISS vektör indeksine gömer.
     6. Orijinal dosyayı AES-256-GCM ile mühürleyerek diske yazar; metadata'yı SQLite'a işler.

2. **Sohbet akışı**
   - Frontend, `/api/chat/ask` uç noktasına SSE üzerinden mesaj yollar.
   - Backend sırasıyla:
     1. Kullanıcı sorgusunu aynı sanitizer hattından geçirir (aktif regülasyonlar + custom kurallar).
     2. FAISS üzerinden bağlamsal chunk'ları çeker.
     3. **Approval Gate** ile kullanıcıya hangi bilgilerin buluta gideceğini gösterir.
     4. Kullanıcı onaylarsa yalnızca **placeholder içeren metni** bulut LLM'e iletir.
     5. Gelen cevabı yerelde **de-anonymizer** üzerinden geçirerek placeholder'ları gerçek değerlere çevirir.
     6. Sonucu SSE ile frontend'e aktarır.

3. **Ayarlar ve regülasyon yönetimi**
   - Settings ekranlarından şunlar yönetilir: LLM / Ollama / Whisper / OCR seçenekleri, varsayılan aktif regülasyonlar, özel recognizer'lar, NER model haritaları ile RAG ve doküman ingest parametreleri.

---

---

## Septum'u Bir AI Gizlilik Geçidi Olarak Kullanmak

Web arayüzünün ötesinde Septum, **LLM tabanlı herhangi bir uygulamanın önüne yerleştirilebilen bir HTTP geçidi (gateway)** olarak çalışabilir. Uygulamanız bulut LLM'e doğrudan çağrı yapmak yerine trafiği önce Septum'a yönlendirir. Septum ise:

1. Aktif regülasyonlara ve özel kurallara göre isteği PII'den arındırır.
2. RAG etkinse anonimleştirilmiş bağlam chunk'larını vektör veritabanından çeker.
3. Yalnızca **maskelenmiş metni** yapılandırılmış LLM sağlayıcısına iletir.
4. Gelen cevaptaki placeholder'ları yerelde tutulan anonimleştirme haritasıyla gerçek değerlere çevirir.

Kavramsal akış:

Uygulamanız → **Septum (anonimleştirme + RAG + onay)** → Bulut LLM
Ham veri ve kişisel bilgiler ortamınızı terk etmez.

Basitleştirilmiş bir örnek:

1. **Uygulamanız** sohbet isteği gönderir:

   ```json
   POST /api/chat/ask
   {
     "messages": [
       { "role": "user", "content": "ACME Corp için son 3 sözleşmeyi özetle ve Ahmet Yılmaz ile ilgili kritik maddeleri çıkar." }
     ],
     "document_ids": [123, 124, 125],
     "metadata": {
       "regulations": ["gdpr", "kvkk"],
       "require_approval": true
     }
   }
   ```

2. **Septum**:
   - Sorgunun ve ilgili dokümanların dilini ve PII içeriğini tespit eder.
   - Kimlik bilgilerini placeholder'larla değiştirir (`[PERSON_1]`, `[ORG_1]` gibi).
   - Gerekirse anonimleştirilmiş chunk'ları vektör veritabanından çeker.
   - Buluta ne gideceğini gösteren bir **onay ekranı** sunabilir.
   - Yalnızca maskelenmiş içeriği, yapılandırılmış LLM sağlayıcısına iletir.

3. **Bulut LLM**, yalnızca placeholder içeren bir cevap döndürür.

4. **Septum**:
   - Bellekteki anonimleştirme haritasını kullanarak placeholder'ları gerçek değerlere çevirir.
   - Nihai, okunabilir cevabı HTTP/SSE üzerinden uygulamanıza iletir.

Bu modda Septum, uygulamalarınız için **tak-çalıştır bir gizlilik katmanı** gibi davranır:

- Mevcut araçlar kendi arayüzünü ve iş mantığını korur.
- PII yönetimi, regülasyon kuralları ve denetlenebilirlik tek merkezde toplanır.
- Arkadaki LLM sağlayıcısını değiştirmek ya da birkaçını aynı anda karıştırmak, uygulamanızın gizlilik modelini bozmaz.

### Otomatik RAG yönlendirme

Sohbet isteğinde `document_ids` verilmediğinde (ya da boş bırakıldığında) Septum, doküman araması yapıp yapmayacağına kendisi karar verir. Yerel Ollama niyet sınıflandırıcısı sorguyu inceler ve `SEARCH` ya da `CHAT` döndürür. Üç yol oluşur:

1. **Manuel RAG** — çağıran taraf `document_ids` verir. Sınıflandırıcı atlanır; retrieval seçilen dokümanlarda eskisi gibi çalışır.
2. **Otomatik RAG** — seçim yok, sınıflandırıcı `SEARCH` diyor ve çoklu-doküman hibrit arama (`_retrieve_chunks_all_documents`) relevans skoru `rag_relevance_threshold` eşiğinin (varsayılan 0,35; RAG ayarlar sekmesinden değiştirilebilir) üzerinde parçalar döndürüyor. Bulunan parçalar tıpkı manuel RAG'daki gibi onay kapısından geçer.
3. **Düz LLM** — seçim yok, sınıflandırıcı `CHAT` diyor ya da hiçbir parça eşiği aşamıyor. LLM'e doküman bağlamı eklenmez.

SSE meta event'i `rag_mode: "manual" | "auto" | "none"` ve `matched_document_ids` alanlarını taşır; dashboard bu bilgiyi kullanarak her mesaja hangi yolun seçildiğini gösteren bir rozet koyar. Çoklu-doküman retrieval kullanıcı sahipliğine saygı duyar — Otomatik RAG yalnızca çağıranın kendi dokümanlarında arama yapar.

---

## Modüler Paket Yerleşimi

Her modül `packages/<ad>/` altında kendi `pyproject.toml`, README ve test paketiyle birlikte gelir. Her paket bağımsız olarak kurulabilir ve test edilebilir (`pip install -e "packages/<ad>[<extra'lar>]"`).

```
packages/
├── core/                 # septum-core (air-gapped; ağ bağımlılığı yok)
│   ├── septum_core/
│   │   ├── detector.py, masker.py, unmasker.py, engine.py
│   │   ├── regulations/
│   │   ├── recognizers/       # 17 regülasyon paketi
│   │   └── national_ids/      # TCKN, SSN, CPF, Aadhaar, IBAN, …
│   ├── tests/
│   └── pyproject.toml          # extra'lar: [transformers], [test]
│
├── mcp/                  # septum-mcp (air-gapped; stdio MCP sunucusu)
│   ├── septum_mcp/server.py, tools.py, config.py
│   └── pyproject.toml          # extra'lar: [test]
│
├── api/                  # septum-api (air-gapped; FastAPI)
│   ├── septum_api/
│   │   ├── main.py              # app factory + lifespan + middleware
│   │   ├── bootstrap.py, config.py, database.py
│   │   ├── models/              # SQLAlchemy ORM modelleri
│   │   ├── routers/             # APIRouter modülleri
│   │   ├── services/            # document pipeline, sanitizer wrapper, …
│   │   ├── middleware/          # auth + rate-limit
│   │   └── utils/               # crypto, device, text_utils, …
│   └── pyproject.toml          # extra'lar: [auth], [rate-limit], [postgres], [server], [test]
│
├── web/                  # septum-web (air-gapped; Next.js 16 panel)
│   ├── src/app/, src/components/, src/store/, src/i18n/
│   └── package.json
│
├── queue/                # septum-queue (köprü)
│   ├── septum_queue/
│   │   ├── base.py               # QueueBackend Protocol + QueueSession
│   │   ├── models.py             # RequestEnvelope / ResponseEnvelope
│   │   ├── file_backend.py       # POSIX atomik rename, air-gapped varsayılanı
│   │   └── redis_backend.py      # Redis Streams consumer groups
│   └── pyproject.toml          # extra'lar: [redis], [test]
│
├── gateway/              # septum-gateway (internet-facing)
│   ├── septum_gateway/
│   │   ├── config.py             # GatewayConfig, env çözünürlük
│   │   ├── forwarder.py          # Anthropic / OpenAI / OpenRouter istemcileri
│   │   ├── response_handler.py   # GatewayConsumer + opsiyonel audit hook
│   │   ├── worker.py             # python -m septum_gateway giriş noktası
│   │   └── main.py               # FastAPI /health (opsiyonel [server] extra'sı)
│   └── pyproject.toml          # ASLA septum-core'a bağımlı değildir
│
└── audit/                # septum-audit (internet-facing)
    ├── septum_audit/
    │   ├── events.py             # AuditRecord envelope
    │   ├── sink.py               # JsonlFileSink + MemorySink
    │   ├── exporters/            # JSON / CSV / Splunk HEC
    │   ├── retention.py          # Yaş + adet limiti, atomik rewrite
    │   ├── consumer.py           # AuditConsumer (queue → sink)
    │   ├── worker.py             # python -m septum_audit giriş noktası
    │   └── main.py               # FastAPI /health + /api/audit/export
    └── pyproject.toml          # ASLA septum-core'a bağımlı değildir
```

**Bağımlılık grafiği** (`→` "bağımlıdır" anlamında):

```
septum-core ← septum-mcp
septum-core ← septum-api ← septum-web (HTTP, yalnız runtime)
septum-queue ← septum-api (producer, septum_api.services.gateway_client üzerinden)
septum-queue ← septum-gateway (consumer + response producer)
septum-queue ← septum-audit[queue] (olay tüketici)
```

`septum-core` bu grafta Python runtime bağımlılığı taşımayan tek pakettir. `septum-queue` de öyle (yalnızca stdlib); Redis backend `[redis]` extra'sıyla etkin olur.

Eski `backend/` uyumluluk katmanı kaldırılmıştır. Her modül artık `packages/` altındadır; import'lar doğrudan `septum_api.*` yoluna gider ve kök `Dockerfile` ile compose varyantları backend kod yolu için `packages/api/`'ye işaret eder.

FastAPI tarafında modern en iyi uygulamalar takip edilir:

- API uç noktaları **APIRouter** ile modüler hâle getirilir.
- İstek ve cevap doğrulaması Pydantic modelleriyle yapılır.
- DB oturumu, ayarlar ve diğer bağımlılıklar `Depends(...)` ile enjekte edilir.
- Tüm path fonksiyonları async'tir; CPU-ağırlıklı işler thread pool içinde koşturulur.

---

---

## Paket İçerikleri

### septum-core

PII motoru: tespit, maskeleme, geri yazma, regülasyon bileşimi. Kod sözleşmesi gereği ağ bağımlılığı yoktur — `septum_core/` dizini altında hiçbir yerde `httpx` / `requests` / `urllib` import edilmez.

- `detector.py` — `Detector` sınıfı çok katmanlı hattı sarar (Presidio → Transformers NER → `SemanticDetectionPort` adaptörü üzerinden isteğe bağlı semantik doğrulama).
- `masker.py`, `unmasker.py` — placeholder üretimi ve geri çevirimi.
- `anonymization_map.py` — coreference çözümlü, oturum bazlı `PII ↔ placeholder` haritası.
- `engine.py` — `SeptumEngine` facade: `engine.mask(text)` / `engine.unmask(text, session_id)`. Uzun süre çalışan MCP alt-işlemleri için TTL tahliyeli bellek içi oturum kayıt defterini içerir.
- `regulations/` — `PolicyComposer` aktif regülasyon ruleset'lerini birleştirir; paket-arası tekrarlayan tanıyıcılar build sırasında deduplicate edilir (~46 → 29 recognizer / mask çağrısı).
- `recognizers/` — 17 regülasyon paketi (GDPR, KVKK, HIPAA, CCPA, LGPD, PIPEDA, PDPA_TH, PDPA_SG, APPI, PIPL, POPIA, DPDP, UK_GDPR, PDPL_SA, NZPA, Australia_PA, CPRA). Her paket kendi `ENTITY_TYPES` sabitini tanımlar; böylece bağımsız motor, API seed'inin gördüğü aynı varlık listesini görür. `recognizers/__init__.py` kanonik `RegulationId` StrEnum'ını, `BUILTIN_REGULATION_IDS` tuple'ını ve api / mcp / standalone giriş noktalarındaki üç tekrarlanan env parser'ın yerini alan `parse_active_regulations_env(value)` helper'ını export eder.
- `national_ids/` — algoritmik checksum'lı ülkeye özgü kimlik doğrulayıcıları (TCKN, SSN, CPF, Aadhaar, IBAN, …).

### septum-mcp

MCP'nin üç standart taşımasından biri üzerinden altı aracı (`mask_text`, `unmask_response`, `detect_pii`, `scan_file`, `list_regulations`, `get_session_map`) açan sunucu:

- **stdio** (varsayılan) — alt-süreç başlatan istemciler için (Claude Code, Claude Desktop, Cursor, Windsurf, Zed).
- **streamable-http** — uzak, container içi ve tarayıcı istemciler için modern HTTP taşıması. Statik bir bearer token ile `septum_mcp.auth.BearerTokenMiddleware` ASGI middleware'i tarafından korunur (sabit-zaman karşılaştırma için `hmac.compare_digest`).
- **sse** — streamable-http'ye henüz geçmemiş eski istemciler için tutulan legacy HTTP + Server-Sent Events taşıması.

Taşıma `--transport` CLI bayrağı ya da `SEPTUM_MCP_TRANSPORT` env değişkeni ile seçilir. HTTP modu ayrıca `--host` / `--port` / `--token` / `--mount-path` bayraklarını ve `SEPTUM_MCP_HTTP_*` env karşılıklarını destekler. `/health` uç noktası koşulsuz 200 OK döner ve bearer kontrolünü atlar; böylece Docker `HEALTHCHECK` ve reverse-proxy probe'ları token olmadan çalışır.

`septum-core`'a bağlıdır; engine ilk araç çağrısında kurulur — boşta dururken neredeyse hiç maliyet üretmez. HTTP modu etkinse `uvicorn` ASGI sunucusu olarak başlatılır; stdio kullanıcıları HTTP stack'ine hiç dokunmaz. Hâlen tek kiracılı — tüm HTTP istemcileri aynı `SeptumEngine`'i ve dolayısıyla aynı anonimleştirme-oturum kayıt defterini paylaşır.

### septum-api

`packages/api/septum_api/` altındaki FastAPI REST katmanı:

- `main.py` — app factory + lifespan + middleware yığını (CORS, auth, rate limit, Prometheus).
- `bootstrap.py`, `config.py`, `database.py` — altyapı yapılandırması (`config.json`), tembel async engine, ayarlar.
- `models/` — SQLAlchemy ORM modelleri (`AppSettings`, `Document`, `Chunk`, `User`, `ApiKey`, `AuditEvent`, …).
- `routers/` — 14 APIRouter modülü (auth, api_keys, chat, chat_sessions, chunks, documents, regulations, settings, setup, users, approval, audit, error_logs, text_normalization).
- `services/` — iş mantığı: `document_pipeline`, `sanitizer` (core wrapper), `llm_router`, `vector_store`, `bm25_retriever`, `deanonymizer`, `prompts`, `approval_gate`, `gateway_client` (queue producer), `ingestion/`, `llm_providers/`, `recognizers/` (core üzerinde adaptör), `national_ids/` (core üzerinde adaptör).
- `middleware/` — `auth.py` (JWT + API key), `rate_limit.py`.
- `utils/` — `crypto.py` (AES-256-GCM), `auth_dependency.py`, `device.py`, `logging_config.py`, `metrics.py`, `text_utils.py`.

### septum-queue

Soyut kuyruk transport; runtime bağımlılığı yoktur:

- `base.py` — `QueueBackend` Protocol + `QueueSession` context manager + `QueueError` / `QueueTimeoutError`.
- `models.py` — `Message`, `RequestEnvelope`, `ResponseEnvelope` dataclass'ları.
- `file_backend.py` — `FileQueueBackend`: tek senkronizasyon primitivi olarak atomik `os.replace`. Topic başına üç dizin (`incoming/`, `processing/`, `done/`); bir mesaj talep etmek, yalnızca bir consumer'ın kazanabileceği bir rename işlemidir.
- `redis_backend.py` — `RedisStreamsQueueBackend`: at-least-once semantiği için consumer group'larla XADD / XREADGROUP / XACK.
- `backend_from_env(topic)` — env-var bazlı dispatch (`SEPTUM_QUEUE_URL` → Redis, `SEPTUM_QUEUE_DIR` → dosya). İkisi de eksikse sessizce default'lama yerine `SystemExit` fırlatır.

### septum-gateway

Internet-facing bölge için bulut LLM yönlendiricisi:

- `config.py` — `GatewayConfig.from_env()` `SEPTUM_GATEWAY_*` env değişkenlerinin yanı sıra eski `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `OPENROUTER_API_KEY` anahtarlarını da okur.
- `forwarder.py` — `BaseForwarder` + `AnthropicForwarder` + `_OpenAICompatibleForwarder` (`OpenAIForwarder`, `OpenRouterForwarder`) + üstel geri çekilmeyle `_post_with_retries`.
- `response_handler.py` — `GatewayConsumer.run_forever()` request envelope'larını cevaplarla eşler; isteğe bağlı `audit_queue` her işlenen istek için PII'siz telemetri envelope'u yayınlar (provider / model / status / latency_ms / correlation_id — prompt, cevap metni veya API key yoktur).
- `worker.py` + `__main__.py` — `python -m septum_gateway` giriş noktası.
- `main.py` — `[server]` extra'sının arkasında FastAPI `/health`.

### septum-audit

Uyumluluk denetim kayıtları:

- `events.py` — `AuditRecord` envelope.
- `sink.py` — `AuditSink` Protocol + `JsonlFileSink` (append-only, logrotate-güvenli, POSIX atomik append) + `MemorySink`.
- `exporters/` — `JsonExporter`, `CsvExporter`, `SplunkHecExporter`; hepsi `BaseExporter(iter_chunks)` streaming primitivini paylaşır.
- `retention.py` — `RetentionPolicy(max_age_days, max_records)` + `.tmp` + `os.replace` ile atomik yerinde JSONL rewrite.
- `consumer.py` — `AuditConsumer(queue, sink)`.
- `worker.py` + `__main__.py` — `python -m septum_audit` giriş noktası.
- `main.py` — FastAPI `/health` + streaming `/api/audit/export`.

---

## Frontend (Next.js App Router) Yapısı

Frontend kök dizini: `packages/web/`

- `src/app/`
  - `layout.tsx` — kök layout
  - `page.tsx` — giriş / yönlendirme sayfası
  - `chat/page.tsx` — backend'e SSE ile bağlı sohbet ekranı
  - `documents/page.tsx` — doküman listesi ve yükleme sayfası
  - `settings/` — alt sayfalar:
    - `page.tsx` — genel ayarlar
    - `regulations/page.tsx` — regülasyon yönetimi
    - `custom-rules/page.tsx` — özel recognizer oluşturma ekranı
- `src/components/`
  - `layout/Sidebar.tsx`, `layout/Header.tsx`
  - `chat/ChatWindow.tsx`, `MessageBubble.tsx`, `ApprovalModal.tsx`, `JsonOutputPanel.tsx`, `DeanonymizationBanner.tsx`
  - `documents/DocumentUploader.tsx`, `DocumentList.tsx`, `DocumentCard.tsx`, `DocumentPreview.tsx`, `TranscriptionPreview.tsx`
  - `settings/*` — `LLMSettings`, `PrivacySettings`, `LocalModelSettings`, `RAGSettings`, `IngestionSettings`, `NERModelSettings`, `RegulationManager`, `CustomRuleBuilder`
- `src/store/`
  - `chatStore.ts`, `documentStore.ts`, `settingsStore.ts`, `regulationStore.ts`
- `src/lib/`
  - `api.ts` — backend HTTP istemcisi
  - `types.ts` — paylaşılan tipler

Next.js tarafında modern en iyi uygulamalar takip edilir:

- App Router (segment tabanlı routing) kullanılır.
- SSE ve streaming cevaplar için `EventSource` ya da `fetch` + `ReadableStream` kullanılır.
- Tailwind CSS; `app`, `components` ve ilgili dizinleri tarayacak şekilde yapılandırılmıştır.

---

## Teknoloji Yığını

**Backend**
- Python, FastAPI, Uvicorn
- Presidio Analyzer/Anonymizer
- HuggingFace Transformers + sentence-transformers
- faiss-cpu
- lingua-language-detector, langdetect
- PaddleOCR, OpenCV, Pillow
- Whisper, ffmpeg-python
- SQLAlchemy + asyncpg (PostgreSQL) / aiosqlite (SQLite)
- Alembic (şema migrasyonları)
- Redis (opsiyonel anonymization map cache)
- cryptography (AES-256-GCM)

**Frontend**
- Next.js 16 (App Router)
- React 19
- TypeScript
- Tailwind CSS
- axios, react-dropzone, lucide-react

**Altyapı**
- Docker Compose (PostgreSQL 16 + Redis 7 + api + web + gateway + audit); `docker-compose*.yml` altında 4 topoloji varyantı
- Ollama (opsiyonel yerel LLM fallback)

---

---

---

## Güvenlik ve Gizlilik

- Ham PII asla log'lanmaz ve buluta gönderilmez.
- Anonymization map (placeholder'lar → gerçek değerler) bellekte, opsiyonel olarak Redis'te ve diskte AES-256-GCM şifreli olarak saklanır. Asla frontend'e, buluta veya log'lara gönderilmez.
- Dosya tipleri uzantıya göre değil, içerik imzasına göre tespit edilir.
- Yüklenen dosyalar diskte AES-256-GCM ile şifreli saklanır; çözme işlemi yalnızca önizleme sırasında ve bellek içinde yapılır.
- Birden fazla regülasyon aynı anda aktifken her zaman **en kısıtlayıcı maskeleme** politikası uygulanır.
- İsteğe bağlı **JWT kimlik doğrulama** (`POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me`): her kullanıcının bir **rolü** vardır (`admin`, `editor`, `viewer`); oturum açıldığında dokümanlar ve sohbet oturumları ilgili kullanıcıya göre filtrelenir; hassas ayar güncellemeleri `admin` rolü gerektirir.

---

## Denetim Kaydı ve Uyumluluk Raporlama

Septum, GDPR, KVKK ve diğer regülasyon uyumluluğu için **salt-ekleme denetim kaydı** tutar:

- **İzlenen olaylar:** PII tespiti (varlık tipi ve sayısı bazında), placeholder geri yazma, doküman yükleme ve silme, regülasyon değişiklikleri.
- **Ham PII saklanmaz:** Denetim olayları yalnızca varlık tipi adlarını ve sayılarını kaydeder — orijinal değerleri asla içermez.
- **Entity köken bağlantısı:** her `EntityDetection` satırı, onu üreten `AuditEvent`'a `audit_event_id` FK'ıyla bağlıdır; böylece dashboard bir audit log girdisinden tam o olayın kapsadığı entity'lere sıçrayabilir.
- **REST API:**
  - `GET /api/audit` — sayfalı; olay tipi, doküman, oturum, tarih aralığı ve **entity tipi** bazında filtrelenebilir (entity tipi filtresi `entity_detections.audit_event_id` üzerinde `EXISTS` correlated subquery ile çalışır).
  - `GET /api/audit/{event_id}/entity-detections` — belirli bir olaya bağlı `EntityDetection` satırlarını döner (bağlanmamış eski olaylar için boş).
  - `GET /api/audit/{document_id}/report` — belirli bir doküman için uyumluluk raporu.
  - `GET /api/audit/session/{session_id}` — bir sohbet oturumunun tam denetim kaydı.
  - `GET /api/audit/metrics` — toplu PII tespit kalite metrikleri (varlık tipi dağılımı, kapsam oranları, doküman başı ortalamalar).
- **Frontend:** Ayarlar → Denetim Kaydı bölümünde olay tipi rozetleri, entity tipi filter dropdown'ı, varlık dağılımları, sayfalama ve her `pii_detected` kartı üzerinde, dokümanı yalnızca o olayın tespit ettiği entity'ler vurgulanmış olarak açan bir **"Bu varlıklara odaklan"** butonu.

---

## LLM Dayanıklılığı ve İzlenebilirlik

- **Devre kesici (circuit breaker):** 120 saniye içinde 3 ardışık bulut LLM hatası sonrası sağlayıcı geçici olarak devre dışı bırakılır (60 saniye bekleme). İstekler yeniden deneme süresini harcamadan doğrudan yerel Ollama fallback'ine atlar. Bekleme süresi sonunda tek bir deneme isteğiyle toparlanma test edilir.
- **Çoklu sağlayıcı desteği:** Anthropic, OpenAI, OpenRouter ve yerel Ollama. Ayarlar UI'ından kod değişikliği olmadan sağlayıcı değiştirilebilir.
- **Üstel geri çekilmeyle yeniden deneme:** Bulut HTTP çağrıları, üstel geri çekilmeyle 3 kez yeniden denenir (0,5sn → 1sn → 2sn).
- **Sağlık uç noktası:** `GET /health` — backend durumu, cihaz bilgisi, LLM sağlayıcısı, Redis bağlantısı ve sağlayıcı bazlı devre kesici durumunu raporlar.

---

## API Referansı

Septum RESTful bir API sunar. Ana uç nokta grupları:

| Grup | Uç noktalar | Açıklama |
|------|-------------|----------|
| **Dokümanlar** | `POST /api/documents`, `GET /api/documents`, `GET /api/documents/{id}`, `GET /api/documents/{id}/raw`, `GET /api/documents/{id}/anon-summary`, `DELETE /api/documents/{id}`, `POST /api/documents/{id}/reprocess` | Yükleme, listeleme, orijinal önizleme / şifre çözme, anonimleştirme özeti, silme, yeniden işleme |
| **Kimlik** | `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me` | JWT bearer hesaplar (roller: admin, editor, viewer) |
| **Sohbet** | `POST /api/chat/ask` (SSE), `GET /api/chat/debug/{session_id}` | Gizlilik korumalı RAG sohbet (streaming) |
| **Sohbet oturumları** | `GET/POST /api/chat-sessions`, `GET/PATCH/DELETE /api/chat-sessions/{id}`, `POST /api/chat-sessions/{id}/messages` | Kalıcı sohbet geçmişi (oturum listesi, meta güncelleme, mesaj ekleme) |
| **Parçalar** | `GET /api/chunks`, `GET /api/chunks/{id}` | Doküman parçalarını arama ve inceleme |
| **Ayarlar** | `GET /api/settings`, `PUT /api/settings` | Uygulama yapılandırması |
| **Regülasyonlar** | `GET /api/regulations`, `PUT /api/regulations/{id}` | Regülasyon kuralları ve özel tanıyıcı yönetimi |
| **Denetim** | `GET /api/audit`, `GET /api/audit/{event_id}/entity-detections`, `GET /api/audit/{document_id}/report`, `GET /api/audit/session/{session_id}`, `GET /api/audit/metrics` | Uyumluluk denetim kaydı, olay bazlı entity kökeni ve tespit metrikleri |
| **Sağlık** | `GET /health`, `GET /metrics` | Sistem sağlığı ve Prometheus metrikleri |

Tam OpenAPI şeması, uygulama çalışırken `http://localhost:3000/docs` adresinde mevcuttur.

---

<p align="center">
  <a href="../readme.md"><strong>🏠 Ana Sayfa</strong></a>
  &nbsp;·&nbsp;
  <a href="installation.md"><strong>🚀 Kurulum</strong></a>
  &nbsp;·&nbsp;
  <a href="benchmark.md"><strong>📈 Benchmark</strong></a>
  &nbsp;·&nbsp;
  <a href="features.md"><strong>✨ Özellikler</strong></a>
  &nbsp;·&nbsp;
  <strong>🏗️ Mimari</strong>
  &nbsp;·&nbsp;
  <a href="document-ingestion.md"><strong>📊 Doküman İşleme</strong></a>
  &nbsp;·&nbsp;
  <a href="screenshots.md"><strong>📸 Ekran Görüntüleri</strong></a>
</p>
