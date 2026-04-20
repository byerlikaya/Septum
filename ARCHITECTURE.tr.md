# Septum — Mimari ve Teknik Referans

> Bu doküman Septum'un iç mimarisini, pipeline detaylarını, kod yapısını ve dağıtım seçeneklerini kapsar.
> Genel bakış ve hızlı başlangıç için [README.tr.md](README.tr.md) dosyasına bakın.

---

## İçindekiler

- [Mimari Genel Bakış](#mimari-genel-bakış)
- [PII Tespiti ve Anonimleştirme Akışı](#pii-tespiti-ve-anonimleştirme-akışı)
- [Septum'u Bir AI Gizlilik Geçidi Olarak Kullanmak](#septumu-bir-ai-gizlilik-geçidi-olarak-kullanmak)
- [Modüler Paket Yerleşimi](#modüler-paket-yerleşimi)
- [Dağıtım Topolojileri](#dağıtım-topolojileri)
- [Paket İçerikleri](#paket-içerikleri)
- [Frontend (Next.js App Router) Yapısı](#frontend-nextjs-app-router-yapısı)
- [Teknoloji Yığını](#teknoloji-yığını)
- [Kurulum](#kurulum)
- [Testleri Çalıştırma](#testleri-çalıştırma)
- [Güvenlik ve Gizlilik](#güvenlik-ve-gizlilik)
- [Denetim Kaydı ve Uyumluluk Raporlama](#denetim-kaydı-ve-uyumluluk-raporlama)
- [LLM Dayanıklılığı ve İzlenebilirlik](#llm-dayanıklılığı-ve-izlenebilirlik)
- [API Referansı](#api-referansı)
- [Yol Haritası / Genişletme](#yol-haritası--genişletme)

---

## Mimari Genel Bakış

Septum **yedi bağımsız kurulabilir paket** olarak `packages/` altında
bölünmüştür, üç bölgeye ayrılır:

- **Air-gapped bölge** (`septum-core`, `septum-mcp`, `septum-api`,
  `septum-web`) — tüm PII işlemleri burada yapılır. Bu paketlerin
  internete çıkan hiçbir bağımlılığı yoktur; `septum-core` ayrıca
  `httpx` / `requests` / `urllib` import'larını yasaklar, böylece ham
  PII kazara dışarı sızamaz.
- **Köprü** (`septum-queue`) — bölgeler arasında yalnızca önceden
  maskelenmiş payload'ları taşır. İki backend: dosya (air-gapped
  varsayılanı) veya Redis Streams (`[redis]` extra'sı). Ham PII kod
  seviyesinde köprüden geçemez.
- **Internet-facing bölge** (`septum-gateway`, `septum-audit`) —
  maskelenmiş LLM isteklerini Anthropic / OpenAI / OpenRouter'a iletir
  ve PII içermeyen uyumluluk telemetrisi yazar. Kod-review
  invariant'ı: bu paketler `septum-core`'u asla import etmez ve
  Dockerfile'lar `packages/core/`'u gateway ve audit image'larına
  kopyalamaz — kısıt image katmanında da uygulanır.

| Bölge | Paket | Rol |
|:---|:---|:---|
| Air-gapped | `septum-core` | PII tespit, maskeleme, demaskeleme, regülasyon motoru. Sıfır ağ bağımlılığı. |
| Air-gapped | `septum-mcp` | Claude Code / Desktop / Cursor'a stdio üzerinden core araçlarını açan MCP sunucu. |
| Air-gapped | `septum-api` | FastAPI REST uç noktaları, doküman pipeline, kimlik doğrulama, hız sınırlama. |
| Air-gapped | `septum-web` | Next.js 16 panel (App Router + React 19). |
| Köprü | `septum-queue` | Soyut `QueueBackend` Protocol + envelope dataclass'ları; dosya / Redis Streams somut backend'ler. |
| Internet-facing | `septum-gateway` | Bulut LLM yönlendiricisi. Kuyruktan maskelenmiş istekleri tüketir ve maskelenmiş cevapları geri yayınlar. |
| Internet-facing | `septum-audit` | Append-only JSONL sink + JSON / CSV / Splunk HEC dışa aktarıcıları. İsteğe bağlı kuyruk tüketici. |

Yüksek seviye akış:

1. **Doküman yükleme**
   - Frontend, `POST /api/documents/upload` ile dosya gönderir.
   - Backend:
     1. Dosya tipini **python-magic** ile tespit eder.
     2. Dil tespiti yapar (lingua + langdetect).
     3. Format'a göre doğru ingester'a yönlendirir (PDF, DOCX, XLSX, ODS, Image, Audio, vb.).
     4. Ortaya çıkan düz metni **PolicyComposer + PIISanitizer** pipeline'ından geçirir.
     5. **Anonimleştirilmiş chunk'lar** üretir ve FAISS'e gömer.
     6. Orijinal dosyayı AES-256-GCM ile şifreleyerek diske yazar; metadata'yı SQLite'ta saklar.

2. **Chat akışı**
   - Frontend, `/api/chat/ask` endpoint'ine SSE ile mesaj gönderir.
   - Backend:
     1. Kullanıcı sorgusunu aynı sanitizer pipeline'ından geçirir (aktif regülasyonlar + custom rules).
     2. FAISS üzerinden bağlamsal chunk'ları çeker.
     3. **Approval Gate** ile hangi bilgilerin buluta gideceğini kullanıcıya gösterir.
     4. Kullanıcı onay verirse, sadece **placeholder içeren metni** bulut LLM'e yollar.
     5. Gelen cevap yerelde **de-anonymizer** üzerinden geçirilerek placeholder'lar gerçek değerlere döner.
     6. Sonuç SSE üzerinden frontend'e iletilir.

3. **Ayarlar ve regülasyon yönetimi**
   - Settings ekranlarından:
     - LLM / Ollama / Whisper / OCR ayarları,
     - Varsayılan aktif regülasyonlar,
     - Custom recognizer'lar,
     - NER model map'leri yönetilir.

---

## PII Tespiti ve Anonimleştirme Akışı

Septum'un kalbinde, aktif regülasyon ve kurallara göre çalışan **çok katmanlı bir PII tespit pipeline'ı** bulunur. Bu yapı; regülasyon odaklı tanıyıcılar, dile duyarlı NER modelleri ve ülke-spesifik doğrulayıcıları tek bir politika altında birleştirir.

Yüksek seviyede akış:

1. **Politika bileşimi**
   - Aktif regülasyon ruleset'leri (ör. GDPR, KVKK, HIPAA, CCPA, LGPD vb.) `PolicyComposer` üzerinden tek bir **bileşik politika** hâline getirilir.
   - Bu politika:
     - Korunması gereken tüm varlık tiplerinin birleşimini,
     - Çalıştırılması gereken (yerleşik + kullanıcı tanımlı) tanıyıcıların listesini içerir.
   - Regex, anahtar kelime veya LLM-tabanlı tüm custom recognizer'lar da bu politika içine enjekte edilir.
   - 17 hazır paket id'si `septum_core` içinde bir `RegulationId` StrEnum'ı ve `BUILTIN_REGULATION_IDS` tuple'ı olarak açılır; downstream paketler (`septum-api`, `septum-mcp`) aynı kanonik kaydı ve ortak bir `parse_active_regulations_env` helper'ını kullanır. Bağımsız `SeptumEngine` varsayılan olarak **17 paketin tamamını** yükler (önceden tam liste sadece API seed'indeydi) ve paket-arası tekrarlanan recognizer'lar policy build sırasında deduplicate edilir (~46 → 29 recognizer / mask çağrısı).

2. **Katman 1 — Presidio tanıyıcıları**
   - Septum, ilk savunma hattı olarak **Microsoft Presidio** kullanır ve tanıyıcı paketlerini regülasyon bazında organize eder.
   - Her bir regülasyon paketi şu alanlar için tanıyıcılar sağlar:
     - Kimlik (isimler, ulusal kimlik numaraları, pasaport vb.)
     - İletişim (e-posta, telefon, adres, IP, URL, sosyal medya hesabı)
     - Finansal tanımlayıcılar (kredi kartı, banka hesabı, IBAN/SWIFT, vergi no)
     - Sağlık, demografik ve kurumsal öznitelikler
   - Kullanıcılar bu katmanı **özel tanıyıcılar** ile genişletebilir (regex desenleri, anahtar kelime listeleri veya LLM-tabanlı kurallar).
   - Ulusal kimlikler ve finansal tanımlayıcılar, yalancı pozitifleri azaltmak için **ülke-spesifik checksum doğrulayıcıları** kullanır.
   - Sadece aktif regülasyonlara ait tanıyıcılar Presidio registry'sine yüklenir.

3. **Katman 2 — Dile özgü NER**
   - Her doküman ve sorgu için dil tespiti yapılır ve gerekirse çok dilli bir yedek modelle birlikte **dile uygun HuggingFace NER modeli** yüklenir.
   - Bu katman:
     - Model çıktısından yalnızca **PERSON_NAME** ve **EMAIL_ADDRESS** etiketlerini eşler; ORG ve LOC etiketleri, yaygın kelimelerde yanlış pozitif oluşturmaması için kasıtlı olarak bastırılır (adres/konum tespiti Presidio'ya devredilir).
     - Son teknoloji XLM-RoBERTa tabanlı modeller kullanır (ör. 20 dil için `Davlan/xlm-roberta-base-wikiann-ner`, Türkçe için `akdeniz27/xlm-roberta-base-turkish-ner`).
     - Tekdüze **0,85** güven eşiği, en az **3 karakter** span uzunluğu ve alt-kelime tokenizasyonundan kaynaklanan bozuk maskelemeyi önlemek için tüm span'leri **kelime sınırlarına** hizalar.
     - NER sonuçları aktif politikanın varlık tiplerine göre filtrelenir; yalnızca aktif regülasyonların gerektirdiği tipler tutulır.
     - 50 karakterden kısa metinler için tamamen atlanır; böylece kısa sorguların aşırı maskelenmesi önlenir.
     - Cihaz farkında çalışır (CUDA → MPS → CPU) ve cache'lenmiş pipeline'lar sayesinde performanslıdır.
   - Hangi dil için hangi modelin kullanılacağı, **NER Models** ayar ekranı üzerinden yapılandırılabilir.
   - İsteğe bağlı **Ollama PII doğrulama katmanı** (Ayarlar → Gizlilik) sorgu anında yanlış pozitif PII adaylarını (örn. genel iş unvanları, rol adları, kurumsal konumlar) filtrelemek için etkinleştirilebilir; böylece yalnızca gerçekten tanımlayıcı bilgiler maskelenir. Presidio'dan gelen ulusal kimlik, IBAN ve telefon tespitleri bu LLM adımına **hiç gönderilmez**; model boş yanıt verirse aday span'ler korunur ki yapısal tanımlayıcılar sızdırılmasın.

4. **Katman 3 — Ollama bağlam-duyarlı katman**
   - Etkinleştirildiğinde (`use_ollama_layer=True`), Septum tamamen **kişi isimleri ve takma adlara** odaklanarak ilk iki katmanın gözden kaçırabileceği PII'leri tespit etmek için **yerel bir Ollama LLM** kullanır:
     - Takma adlar ve gayriresmi atıflar (ör. daha önce "John Doe" tespit edilmişse "john").
     - Bu katmandan yalnızca PERSON_NAME, ALIAS, FIRST_NAME ve LAST_NAME tip çıktıları kabul edilir.
   - Bu katman tam büyük/küçük harf uyumunu korur ve tamamen cihaz üzerinde çalışır; böylece hiçbir PII yerel makineden çıkmaz.
   - 80 karakterden kısa metinler için atlanır. Sayısal ağırlıklı yapılandırılmış içerik (ör. fiyat listeleri, faturalar) için gürültülü tespitleri önlemek amacıyla devre dışı bırakılır.

5. **Anonimleştirme ve coreference**
   - Yukarıdaki katmanlardan çıkan tüm span'ler birleştirilir, yinelenenler ayıklanır ve `AnonymizationMap` içine aktarılır:
     - Her benzersiz varlık için kararlı bir placeholder atanır (ör. `[PERSON_1]`, `[EMAIL_2]`).
     - Coreference mantığı sayesinde aynı kişiye ait tekrar eden atıflar (tam isim → sadece isim gibi) **aynı** placeholder ile eşleştirilir.
     - **Blocklist** yalnızca kişi-tanımlayıcı varlık tipleriyle (PERSON_NAME, FIRST_NAME, LAST_NAME, ALIAS, USERNAME) sınırlandırılmıştır; böylece yaygın kelimelerin kolateral hasar olarak maskelenmesi önlenir.
   - Anonymization map asla belleğin dışına çıkmaz ve diske yazılmaz.

6. **Çoklu regülasyon çatışmalarının ele alınması**
   - Birden fazla regülasyon aynı anda aktif olduğunda Septum her zaman **en kısıtlayıcı** maskeleme davranışını uygular:
     - Herhangi bir regülasyon bir değeri PII olarak işaretliyorsa, o değer PII kabul edilir.
     - Çakışan varlıklar tek bir placeholder altında birleştirilirken, hangi regülasyonların bu kararı tetiklediğine dair metadata korunur.

---

## Septum'u Bir AI Gizlilik Geçidi Olarak Kullanmak

Web arayüzünün ötesinde Septum, **herhangi bir LLM tabanlı uygulamanın önüne konumlanabilen bir HTTP geçidi (gateway)** olarak da çalışabilir. Uygulamanız bulut LLM'e doğrudan çağrı yapmak yerine, tüm trafiği önce Septum'a yönlendirir:

1. Gelen istek, etkin regülasyonlar ve özel kurallara göre PII'den arındırılır.
2. RAG etkinse, anonimleştirilmiş context chunk'ları vektör veritabanından çekilir.
3. Yalnızca **maskelenmiş metin** yapılandırılmış LLM sağlayıcısına iletilir.
4. Dönen cevap, yerelde tekrar anonimleştirme haritası kullanılarak gerçek değerlere map edilir.

Kavramsal akış:

Uygulamanız → **Septum (anonimleştir + RAG + onay)** → Bulut LLM
Ham veri ve kişisel bilgiler ortamınızı terketmez.

Basitleştirilmiş bir örnek akış:

1. **Uygulamanız**, sohbet isteği gönderir:

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
   - Kimlik bilgilerini placeholder'larla değiştirir (ör. `[PERSON_1]`, `[ORG_1]`).
   - Gerekirse anonimleştirilmiş chunk'ları vektör veritabanından çeker.
   - Hangi bilgilerin buluta gideceğini gösteren bir **onay ekranı** sunabilir.
   - Yalnızca maskeli içeriği, yapılandırılmış LLM sağlayıcısına iletir.

3. **Bulut LLM**, sadece placeholder içeren bir cevap döner.

4. **Septum**:
   - Bellekteki anonimleştirme haritasını kullanarak placeholder'ları tekrar gerçek değerlere çevirir.
   - Nihai, okunabilir cevabı HTTP/SSE üzerinden uygulamanıza iletir.

Bu modda Septum, uygulamalarınız için **tak-çalıştır bir gizlilik katmanı** gibi davranır:

- Mevcut araçlar kendi arayüz ve iş mantıklarını korur.
- PII yönetimi, regülasyon kuralları ve denetlenebilirlik tek bir merkezi noktada toplanır.
- Arkada LLM sağlayıcısını değiştirmek veya birden fazla sağlayıcıyı karıştırmak, uygulama tarafındaki gizlilik modelini bozmaz.

### Otomatik RAG yönlendirme

Chat isteğinde `document_ids` verilmediğinde (veya boş bırakıldığında) Septum, doküman araması yapacağına mı yoksa düz sohbet botu gibi mi yanıt vereceğine otomatik karar verir. Yerel Ollama niyet sınıflandırıcısı sorguyu inceleyerek `SEARCH` veya `CHAT` döndürür. Üç yol oluşur:

1. **Manuel RAG** — çağıran taraf `document_ids` gönderir. Sınıflandırıcı atlanır; retrieval seçilen dokümanlar üzerinde eskisi gibi çalışır.
2. **Otomatik RAG** — seçim yok, sınıflandırıcı `SEARCH` diyor ve çoklu-doküman hibrit arama (`_retrieve_chunks_all_documents`) relevans skoru `rag_relevance_threshold` eşiğinin (varsayılan 0.35, RAG ayarlar sekmesinden yapılandırılır) üzerinde parçalar döndürüyor. Bulunan parçalar aynı manuel RAG gibi onay kapısından geçer.
3. **Düz LLM** — seçim yok, sınıflandırıcı `CHAT` diyor ya da hiçbir parça eşiği aşamıyor. LLM'e doküman bağlamı eklenmez.

SSE meta event'i `rag_mode: "manual" | "auto" | "none"` ve `matched_document_ids` alanlarını taşır; dashboard bunu kullanarak her mesajda hangi yolun seçildiğini gösteren bir rozet çizer. Çoklu-doküman retrieval kullanıcı sahipliğine saygı gösterir — Otomatik RAG sadece çağıranın kendi dokümanlarında arar.

---

## Modüler Paket Yerleşimi

Her modül `packages/<ad>/` altında kendi `pyproject.toml`, README ve
test paketi ile yaşar. Her biri izole halde kurulabilir ve test
edilebilir (`pip install -e "packages/<ad>[<ek-bileşenler>]"`).

```
packages/
├── core/                 # septum-core (air-gapped; sıfır ağ deps)
│   ├── septum_core/
│   │   ├── detector.py, masker.py, unmasker.py, engine.py
│   │   ├── regulations/
│   │   ├── recognizers/       # 17 regülasyon paketi
│   │   └── national_ids/      # TCKN, SSN, CPF, Aadhaar, IBAN, …
│   ├── tests/
│   └── pyproject.toml          # ek bileşenler: [transformers], [test]
│
├── mcp/                  # septum-mcp (air-gapped; stdio MCP sunucu)
│   ├── septum_mcp/server.py, tools.py, config.py
│   └── pyproject.toml          # ek bileşenler: [test]
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
│   └── pyproject.toml          # ek bileşenler: [auth], [rate-limit], [postgres], [server], [test]
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
│   └── pyproject.toml          # ek bileşenler: [redis], [test]
│
├── gateway/              # septum-gateway (internet-facing)
│   ├── septum_gateway/
│   │   ├── config.py             # GatewayConfig, env çözünürlük
│   │   ├── forwarder.py          # Anthropic / OpenAI / OpenRouter istemcileri
│   │   ├── response_handler.py   # GatewayConsumer + isteğe bağlı audit hook
│   │   ├── worker.py             # python -m septum_gateway giriş noktası
│   │   └── main.py               # FastAPI /health (isteğe bağlı [server] ek bileşeni)
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

`septum-core` Septum graf'ı içindeki sıfır Python runtime bağımlılığı
olan tek pakettir. `septum-queue` de gerekli bağımlılıkla gelmez
(stdlib only); Redis backend `[redis]` ek bileşeni ile kullanılabilir.

Eski `backend/` uyumluluk katmanı kaldırılmıştır. Her modül artık
`packages/` altındadır, import'lar doğrudan `septum_api.*`'a gider ve
kök `Dockerfile` + compose varyantları backend kod yolu için
`packages/api/`'ye işaret eder.

FastAPI tarafında Context7 en iyi pratikleri takip edilir:

- API endpoint'leri **APIRouter** ile modüler hâle getirilir.
- İstek/cevap doğrulaması Pydantic modellerle yapılır.
- DB oturumu, ayarlar ve diğer bağımlılıklar `Depends(...)` ile enjekte edilir.
- Tüm path fonksiyonları async'tir; CPU-ağırlıklı işler thread pool içinde çalıştırılır.

---

## Dağıtım Topolojileri

Dört Docker Compose varyantı her dağıtım şeklini kapsar; dördü de
`docker-compose config` ile doğrulanır ve Docker 29+ üzerinde
temiz build eder (linux/amd64 + linux/arm64).

| Topoloji | Dosya | İçerik | Ne zaman kullanılır |
|:---|:---|:---|:---|
| Standalone | `docker-compose.standalone.yml` | `docker/standalone.Dockerfile`'dan tek container, SQLite | En basit kurulum; `byerlikaya/septum:latest` olarak yayınlanır. |
| Full dev stack | `docker-compose.yml` | api + web + gateway-worker + audit-worker + audit-api + Postgres + Redis + isteğe bağlı Ollama profili | Yerel geliştirme veya bölge mantığı ile tek-host kurulum. |
| Air-gapped bölge | `docker-compose.airgap.yml` | api + web + Postgres + Redis (gateway yok). `USE_GATEWAY_DEFAULT=true` bulut çağrılarını Redis Streams üzerinden yönlendirir. | İki-host ayrık kurulumunun iç host'u. |
| Internet-facing bölge | `docker-compose.gateway.yml` | gateway-worker + gateway-health + audit-worker + audit-api + Redis. YAML anchor'lar (`x-gateway-base`, `x-audit-base`) ile servis tanımları tekrarı kaldırılmıştır. | İki-host ayrık kurulumunun DMZ / bulut host'u. |

Gerçek bir air-gapped kurulum için `airgap.yml`'ı iç host'ta ve
`gateway.yml`'ı DMZ host'ta çalıştırın ve VPN / özel link üzerinden
ikisini de aynı Redis'e bağlayın. Kuyruk yalnızca maskelenmiş metin
taşır; ham PII köprüyü asla geçmez.

**Modül başına Dockerfile'lar** `docker/` altında — `api.Dockerfile`,
`web.Dockerfile`, `gateway.Dockerfile`, `audit.Dockerfile`,
`mcp.Dockerfile`, `standalone.Dockerfile`. Gateway ve audit image'ları
hafiftir (~250 MB, torch / Presidio / spaCy yok) ve — image-katmanı
sözleşmesi olarak — runtime stage'e `packages/core/` kopyalamazlar.
api ve standalone image'ları tam ML stack'i taşır (CPU-only torch,
Presidio, spaCy, PaddleOCR, Whisper, FAISS, BM25) — sırasıyla ~9.8 GB
ve ~5.7 GB.

Her HTTP servisi
`python -c "import urllib.request; urllib.request.urlopen('http://.../health')"`
tabanlı bir Docker `HEALTHCHECK` ile gelir (web image'ı
`node:20-alpine` üzerinde çalıştığı için `wget` kullanır). MCP
image'ında HEALTHCHECK yoktur — stdio-only; liveness alt-işlem exit
kodudur.

---

## Paket İçerikleri

### septum-core

PII motoru: tespit, maskeleme, demaskeleme, regülasyon kompozisyonu.
Sözleşme gereği sıfır ağ bağımlılığı — `septum_core/` altında hiçbir
yerde `httpx` / `requests` / `urllib` import edilmez.

- `detector.py` — `Detector` sınıfı çok katmanlı pipeline'ı sarar
  (Presidio → Transformers NER → `SemanticDetectionPort` adaptörü
  üzerinden isteğe bağlı semantik doğrulama).
- `masker.py`, `unmasker.py` — placeholder üretimi ve geri çevirimi.
- `anonymization_map.py` — coreference çözümlü oturum bazlı
  `PII ↔ placeholder` haritası.
- `engine.py` — `SeptumEngine` facade: `engine.mask(text)` /
  `engine.unmask(text, session_id)`. Uzun çalışan MCP alt-işlemleri
  için TTL tahliyeli bellek içi oturum kayıt defteri içerir.
- `regulations/` — `PolicyComposer` aktif regülasyon ruleset'lerini
  birleştirir; paket-arası tekrarlayan tanıyıcılar build sırasında
  deduplicate edilir (~46 → 29 recognizer / mask çağrısı).
- `recognizers/` — 17 regülasyon paketi (GDPR, KVKK, HIPAA, CCPA,
  LGPD, PIPEDA, PDPA_TH, PDPA_SG, APPI, PIPL, POPIA, DPDP, UK_GDPR,
  PDPL_SA, NZPA, Australia_PA, CPRA). Her paket kendi `ENTITY_TYPES`
  sabitini tanımlar; böylece bağımsız motor, API seed'inin gördüğü
  aynı varlık listesini görür. `recognizers/__init__.py` kanonik
  `RegulationId` StrEnum'ını, `BUILTIN_REGULATION_IDS` tuple'ını ve
  api / mcp / standalone giriş noktalarındaki üç tekrarlanmış env
  parser'ın yerine geçen `parse_active_regulations_env(value)`
  helper'ını export eder.
- `national_ids/` — algoritmik checksum'lı ülke-özgü kimlik
  doğrulayıcıları (TCKN, SSN, CPF, Aadhaar, IBAN, …).

### septum-mcp

Claude Code / Desktop / Cursor'a altı araç sunan stdio MCP sunucu:
`mask_text`, `unmask_response`, `detect_pii`, `scan_file`,
`list_regulations`, `get_session_map`. `septum-core`'a bağımlıdır;
motor inşası ilk araç çağrısına ertelenir — boşta duran maliyet sıfıra
yakın.

### septum-api

`packages/api/septum_api/` altında FastAPI REST katmanı:

- `main.py` — app factory + lifespan + middleware yığını (CORS, auth,
  rate limit, Prometheus).
- `bootstrap.py`, `config.py`, `database.py` — altyapı yapılandırması
  (`config.json`), tembel async engine, ayarlar.
- `models/` — SQLAlchemy ORM modelleri (`AppSettings`, `Document`,
  `Chunk`, `User`, `ApiKey`, `AuditEvent`, …).
- `routers/` — 14 APIRouter modülü (auth, api_keys, chat,
  chat_sessions, chunks, documents, regulations, settings, setup,
  users, approval, audit, error_logs, text_normalization).
- `services/` — iş mantığı: `document_pipeline`, `sanitizer` (core
  wrapper), `llm_router`, `vector_store`, `bm25_retriever`,
  `deanonymizer`, `prompts`, `approval_gate`, `gateway_client` (queue
  producer), `ingestion/`, `llm_providers/`, `recognizers/` (core
  üzerinde adaptör), `national_ids/` (core üzerinde adaptör).
- `middleware/` — `auth.py` (JWT + API key), `rate_limit.py`.
- `utils/` — `crypto.py` (AES-256-GCM), `auth_dependency.py`,
  `device.py`, `logging_config.py`, `metrics.py`, `text_utils.py`.

### septum-queue

Soyut kuyruk transport, sıfır runtime bağımlılığı:

- `base.py` — `QueueBackend` Protocol + `QueueSession` context
  manager + `QueueError` / `QueueTimeoutError`.
- `models.py` — `Message`, `RequestEnvelope`, `ResponseEnvelope`
  dataclass'ları.
- `file_backend.py` — `FileQueueBackend`: atomik `os.replace` tek
  senkronizasyon primitivi. Topic başına üç dizin (`incoming/`,
  `processing/`, `done/`); bir mesajı talep etmek, yalnızca bir
  consumer'ın kazanabileceği bir rename'dir.
- `redis_backend.py` — `RedisStreamsQueueBackend`: at-least-once
  semantiği için consumer group'larla XADD / XREADGROUP / XACK.
- `backend_from_env(topic)` — env-var dispatch (`SEPTUM_QUEUE_URL` →
  Redis, `SEPTUM_QUEUE_DIR` → dosya). İkisi de eksik ise sessizce
  default'lama yerine `SystemExit` fırlatır.

### septum-gateway

Internet-facing bölge için bulut LLM yönlendiricisi:

- `config.py` — `GatewayConfig.from_env()` `SEPTUM_GATEWAY_*` env
  değişkenleri + eski `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` /
  `OPENROUTER_API_KEY`'leri okur.
- `forwarder.py` — `BaseForwarder` + `AnthropicForwarder` +
  `_OpenAICompatibleForwarder` (`OpenAIForwarder`,
  `OpenRouterForwarder`) + exponential backoff ile
  `_post_with_retries`.
- `response_handler.py` — `GatewayConsumer.run_forever()` request
  envelope'larını cevaplarla eşler; isteğe bağlı `audit_queue` her
  işlenen istek için PII'siz telemetri envelope'u yayınlar (provider /
  model / status / latency_ms / correlation_id — prompt yok, cevap
  metni yok, api key yok).
- `worker.py` + `__main__.py` — `python -m septum_gateway` giriş noktası.
- `main.py` — `[server]` ek bileşeninin arkasında FastAPI `/health`.

### septum-audit

Uyumluluk denetim kayıtları:

- `events.py` — `AuditRecord` envelope.
- `sink.py` — `AuditSink` Protocol + `JsonlFileSink` (append-only,
  logrotate-güvenli, POSIX atomik append) + `MemorySink`.
- `exporters/` — `JsonExporter`, `CsvExporter`, `SplunkHecExporter`
  hepsi `BaseExporter(iter_chunks)` streaming primitivini paylaşır.
- `retention.py` — `RetentionPolicy(max_age_days, max_records)` +
  `.tmp` + `os.replace` ile atomik yerinde JSONL rewrite.
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
    - `custom-rules/page.tsx` — custom recognizer oluşturma ekranı
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

Next.js tarafında Context7 en iyi pratikleri takip edilir:

- App Router (segment tabanlı routing) kullanılır.
- SSE ve streaming cevaplar için `EventSource` veya `fetch` + `ReadableStream` kullanılır.
- Tailwind CSS, `app`, `components` ve ilgili dizinleri tarayacak şekilde yapılandırılmıştır.

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

## Kurulum

### Seçenek A: Docker Compose (önerilen)

Septum'u tüm bağımlılıklarıyla (PostgreSQL, Redis) çalıştırmanın en hızlı yolu:

```bash
docker compose up
```

Kurulum sihirbazı ilk ziyarette tüm yapılandırmayı halleder. Bu komut şunları başlatır:
- **PostgreSQL 16** — production veritabanı
- **Redis 7** — çoklu worker desteği için anonymization map cache
- **Septum** (port 3000) — Backend + Frontend tek container, tek port

Yerel Ollama instance'ı eklemek için:

```bash
docker compose --profile ollama up
```

### Seçenek B: Yerel geliştirme

#### 1. Ortak ön gereksinimler

- Python 3.11+ (3.13'e kadar test edilmiştir)
- Node.js 20+ (Next.js 16 için)
- ffmpeg (Whisper için)

#### 2. Tek-adımlı kurulum

```bash
./dev.sh --setup     # tüm modüler paketleri (editable) + packages/api/requirements.txt + npm'i kurar
```

`dev.sh --setup` her `packages/*` modülünü geliştirme ek-bileşenleri
ile editable modda yükler (`septum-core[transformers,test]`,
`septum-queue[redis,test]`, `septum-api[auth,rate-limit,postgres,server,test]`,
`septum-mcp[test]`, `septum-gateway[server,test]`,
`septum-audit[queue,server,test]`), ardından ağır ML / OCR / Whisper /
ingestion bağımlılıklarını `packages/api/requirements.txt`'ten çeker.

#### 3. Dev stack'i başlat

```bash
./dev.sh             # api (septum_api.main:app, port 8000) + web (packages/web, port 3000) başlatır
```

Varsayılan olarak her şey tek port üzerinden sunulur:
- UI + API: `http://localhost:3000`
- API dökümantasyonu: `http://localhost:3000/docs`

Next.js rewrites, `/api/*`, `/docs`, `/health` ve `/metrics` isteklerini container içindeki backend'e (port 8000) proxy eder. Port 8000 dışarıya açılmaz.

`packages/web/src/lib/api.ts` içindeki API base URL, build-time
`NEXT_PUBLIC_API_BASE_URL` env değişkeniyle yönlendirilir. Ayarsız
olması aynı-origin proxy (varsayılan) demektir. Ayrık dağıtım için
(api ve web farklı host'larda) build-time'da api'nin public URL'ini
ayarlayın.

Tüm yapılandırma ilk çalıştırmada kurulum sihirbazı tarafından yapılır.
Şifreleme anahtarları ve altyapı ayarlarını içeren bir `config.json`
dosyası otomatik oluşturulur (varsayılan konum: kök `config.json`;
`SEPTUM_CONFIG_PATH` ile override edebilirsiniz). Manuel yapılandırma
dosyasına gerek yoktur.

**İlk açılış:** Arayüz, uygulama ayarlarında kurulum tamamlanana kadar kısa bir kurulum sihirbazı (LLM sağlayıcı ve bağlantı testi) gösterir. Sohbetler sunucuda saklanır (`/api/chat-sessions`) ve sohbet kenar çubuğundan değiştirilebilir.

#### 4. Yerel durumu sıfırla

```bash
./dev.sh --reset     # DB, config.json, uploads, indexes, anon_maps'i siler (top-level runtime state)
```

---

## Testleri Çalıştırma

Testler iki yerde yaşar:

- **Modüler paket testleri** `packages/<ad>/tests/` altında — izole,
  hızlı ve kurulum-bağımsız (her `pytest packages/<ad>/tests/` reponun
  geri kalanı olmadan çalışır).
- **API entegrasyon testleri** `packages/api/tests/` altında — tam
  doküman + chat pipeline'ını uçtan uca işletir. Bootstrap, database,
  router'lar, servisler, utils ve auth middleware da burada kapsanır.

```bash
# Her şey (shell glob genişlemesi gerekir — 'pytest packages/' tek
# başına paylaşılan 'tests' namespace'i üzerinden takılır)
pytest packages/*/tests -q

# Tek bir modüler paket
pytest packages/core/tests/ -q
pytest packages/queue/tests/ -q
pytest packages/gateway/tests/ -q
pytest packages/audit/tests/ -q
pytest packages/mcp/tests/ -q
pytest packages/api/tests/ -q
```

Claude Code içindeki `/test` beceri, değişen kaynağa göre doğru test
dosyasını seçer. Örnekler:
- `packages/api/septum_api/services/sanitizer.py` → `packages/api/tests/test_sanitizer.py`
- `packages/queue/septum_queue/file_backend.py` → `packages/queue/tests/test_file_backend.py`
- `packages/audit/septum_audit/retention.py` → `packages/audit/tests/test_retention.py`

**Sürekli entegrasyon:** `.github/workflows/tests.yml` paralel bir
matriks çalıştırır — `backend-tests` (her paketi editable pip install
eder + `packages/api/requirements.txt` + pytest `packages/api/tests`),
`modular-tests` (her paket kendi step'inde yüklenir ve test edilir),
artı backend lint (Ruff + Bandit), backend security (`pip-audit`),
frontend Jest, frontend typecheck (`tsc --noEmit`), frontend
`npm audit`.

Gerçek bulut LLM sağlayıcılarına istek gönderecek tüm testler **mock** edilmelidir; gerçek dış servis çağrısı yapan testler hata olarak değerlendirilir.

---

## Güvenlik ve Gizlilik

- Ham PII asla log'lanmaz ve buluta gönderilmez.
- Anonymization map (maskeler → gerçek değerler) bellekte, opsiyonel olarak Redis'te ve diskte AES-256-GCM şifreli olarak saklanır. Asla frontend'e, buluta veya log'lara gönderilmez.
- Dosya tipleri uzantıya göre değil, içerik imzasına göre tespit edilir.
- Yüklenen dosyalar diskte AES-256-GCM ile şifreli saklanır; çözme işlemi sadece önizleme sırasında ve bellek içinde yapılır.
- Birden fazla regülasyon aynı anda aktifken, her zaman **en kısıtlayıcı maskeleme** politikası uygulanır.
- İsteğe bağlı **JWT kimlik doğrulama** (`POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me`): her kullanıcının bir **rolü** vardır (`admin`, `editor`, `viewer`); oturum açıldığında dokümanlar ve sohbet oturumları ilgili kullanıcıya göre filtrelenir; hassas ayar güncellemeleri `admin` gerektirir.

---

## Denetim Kaydı ve Uyumluluk Raporlama

Septum, GDPR, KVKK ve diğer regülasyon uyumluluğu için **salt-ekleme denetim kaydı** tutar:

- **İzlenen olaylar:** PII tespiti (varlık tipi ve sayısı bazında), de-anonimleştirme, doküman yükleme/silme, regülasyon değişiklikleri.
- **Ham PII saklanmaz:** Denetim olayları yalnızca varlık tipi adlarını ve sayılarını kaydeder — asla orijinal değerleri içermez.
- **REST API:**
  - `GET /api/audit` — sayfalı, olay tipi, doküman, oturum ve tarih aralığına göre filtrelenebilir.
  - `GET /api/audit/{document_id}/report` — belirli bir doküman için uyumluluk raporu.
  - `GET /api/audit/session/{session_id}` — bir sohbet oturumunun tam denetim kaydı.
  - `GET /api/audit/metrics` — toplu PII tespit kalite metrikleri (varlık tipi dağılımı, kapsam oranları, doküman başı ortalamalar).
- **Frontend:** Ayarlar → Denetim Kaydı bölümünde olay tipi rozetleri, varlık dağılımları ve sayfalama ile denetim günlüğü görüntüleyici.

---

## LLM Dayanıklılığı ve İzlenebilirlik

- **Devre kesici (circuit breaker):** 120 saniye içinde 3 ardışık bulut LLM hatası sonrası sağlayıcı geçici olarak devre dışı bırakılır (60 saniye bekleme). İstekler yeniden deneme süresini harcamadan doğrudan yerel Ollama fallback'e atlar. Bekleme süresi sonunda tek bir deneme isteğiyle toparlanma test edilir.
- **Çoklu sağlayıcı desteği:** Anthropic, OpenAI, OpenRouter ve yerel Ollama. Kod değişikliği olmadan Ayarlar UI'dan sağlayıcı değiştirme.
- **Üstel geri çekilme ile yeniden deneme:** Bulut HTTP çağrıları üstel geri çekilmeyle 3 kez yeniden denenir (0,5s → 1s → 2s).
- **Sağlık endpoint'i:** `GET /health` — backend durumu, cihaz bilgisi, LLM sağlayıcısı, Redis bağlantısı ve sağlayıcı bazlı devre kesici durumu raporlar.

---

## API Referansı

Septum RESTful bir API sunar. Ana endpoint grupları:

| Grup | Endpoint'ler | Açıklama |
|------|-------------|----------|
| **Dokümanlar** | `POST /api/documents`, `GET /api/documents`, `GET /api/documents/{id}`, `GET /api/documents/{id}/raw`, `GET /api/documents/{id}/anon-summary`, `DELETE /api/documents/{id}`, `POST /api/documents/{id}/reprocess` | Yükleme, listeleme, orijinal önizleme/şifre çözme, anonimleştirme özeti, silme, yeniden işleme |
| **Kimlik** | `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me` | JWT bearer hesaplar (roller: admin, editor, viewer) |
| **Sohbet** | `POST /api/chat/ask` (SSE), `GET /api/chat/debug/{session_id}` | Gizlilik korumalı RAG sohbet (streaming) |
| **Sohbet oturumları** | `GET/POST /api/chat-sessions`, `GET/PATCH/DELETE /api/chat-sessions/{id}`, `POST /api/chat-sessions/{id}/messages` | Kalıcı sohbet geçmişi (oturum listesi, meta güncelleme, mesaj ekleme) |
| **Parçalar** | `GET /api/chunks`, `GET /api/chunks/{id}` | Doküman parçalarını arama ve inceleme |
| **Ayarlar** | `GET /api/settings`, `PUT /api/settings` | Uygulama yapılandırması |
| **Regülasyonlar** | `GET /api/regulations`, `PUT /api/regulations/{id}` | Regülasyon kuralları ve özel tanıyıcı yönetimi |
| **Denetim** | `GET /api/audit`, `GET /api/audit/{id}/report`, `GET /api/audit/metrics` | Uyumluluk denetim kaydı ve tespit metrikleri |
| **Sağlık** | `GET /health`, `GET /metrics` | Sistem sağlığı ve Prometheus metrikleri |

Tam OpenAPI şeması, uygulama çalışırken `http://localhost:3000/docs` adresinde mevcuttur.

---

## Yol Haritası / Genişletme

- Yeni ülke regülasyonları için recognizer registry tarafında yeni regulation pack'ler eklenebilir.
- Yeni ulusal kimlik formatları için national ID modülünde yeni validator ve recognizer eklenebilir.
- Yeni doküman formatları için ingestion katmanında özel ingester implementasyonları eklenebilir.
- NER model haritası, Settings → NER Models ekranından kullanıcı tarafından güncellenebilir.
- Yerel LLM (Ollama) ile zamir coreference çözümleme, dolaylı kişi referanslarını tespit eder.
- PII tespit kalite metrikleri ile tespit kapsamının veri odaklı değerlendirmesi.
