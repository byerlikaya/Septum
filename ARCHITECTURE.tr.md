# Septum — Mimari ve Teknik Referans

> Bu doküman Septum'un iç mimarisini, pipeline detaylarını, kod yapısını ve dağıtım seçeneklerini kapsar.
> Genel bakış ve hızlı başlangıç için [README.tr.md](README.tr.md) dosyasına bakın.

---

## İçindekiler

- [Mimari Genel Bakış](#mimari-genel-bakış)
- [PII Tespiti ve Anonimleştirme Akışı](#pii-tespiti-ve-anonimleştirme-akışı)
- [Septum'u Bir AI Gizlilik Geçidi Olarak Kullanmak](#septumu-bir-ai-gizlilik-geçidi-olarak-kullanmak)
- [Backend (FastAPI) Yapısı](#backend-fastapi-yapısı)
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

- **Backend**: Python + FastAPI — doküman işleme, anonimleştirme, şifreleme ve LLM entegrasyonu burada çalışır. Tüm veri işleme ve PII koruma mantığı sunucu tarafındadır.
- **Frontend**: Next.js 16 + React 19 — chat, doküman yönetimi, ayarlar ve regülasyon ekranlarını sunan web arayüzü. Backend ile HTTP ve SSE (stream) üzerinden haberleşir.

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

---

## Backend (FastAPI) Yapısı

Backend kök dizini: `backend/`

- `app/main.py` — FastAPI uygulamasının tanımı ve router kayıtları
- `app/config.py` — Pydantic Settings ile konfigürasyon
- `app/database.py` — SQLite bağlantısı ve `RegulationRuleset` başlangıç verisi (seed)
- `app/models/` — SQLAlchemy modelleri:
  - `document.py`, `chunk.py`, `settings.py`, `regulation.py`, `custom_recognizer.py`
- `app/schemas/` — Pydantic şemaları:
  - `document.py`, `chat.py`, `settings.py`, `regulation.py`, `custom_recognizer.py`
- `app/routers/` — FastAPI router'ları:
  - `documents.py`, `chunks.py`, `chat.py`, `approval.py`, `settings.py`, `regulations.py`, `error_logs.py`, `text_normalization.py`
- `app/services/`:
  - `ingestion/` — format-spesifik ingester'lar (PDF, DOCX, XLSX, ODS, görüntü/OCR, ses/Whisper)
  - `recognizers/` — regülasyon paketleri (gdpr, hipaa, kvkk, lgpd, ccpa, …) ve `registry.py`
  - `national_ids/` — ülkelere özgü kimlik doğrulayıcıları (TCKN, SSN, CPF, Aadhaar, IBAN vb.)
  - `policy_composer.py` — aktif regülasyon ve özel kuralları tek bir politika hâline getirir
  - `ner_model_registry.py` — dil → model eşlemesi ve lazy loading
  - `sanitizer.py` — PII tespit ve placeholder pipeline'ı
  - `anonymization_map.py` — oturum bazlı anonimleştirme haritası + coreference yönetimi
  - `document_pipeline.py`, `vector_store.py`, `llm_router.py`, `deanonymizer.py`, `approval_gate.py`
  - `prompts.py` — merkezileştirilmiş LLM prompt kataloğu
  - `error_logger.py`, `ollama_client.py`, `non_pii_filter.py`, `text_normalizer.py`
- `app/utils/`:
  - `device.py` — CPU/MPS/CUDA seçimi
  - `crypto.py` — AES-256-GCM dosya şifreleme
  - `text_utils.py` — Unicode NFC + locale-farkında küçültme (lowercase)
- `tests/` — pytest senaryoları (sanitizer, anonymization_map, national_ids, policy_composer, deanonymizer, llm_router, crypto, ingesters vb.).

FastAPI tarafında Context7 en iyi pratikleri takip edilir:

- API endpoint'leri **APIRouter** ile modüler hâle getirilir.
- İstek/cevap doğrulaması Pydantic modellerle yapılır.
- DB oturumu, ayarlar ve diğer bağımlılıklar `Depends(...)` ile enjekte edilir.
- Tüm path fonksiyonları async'tir; CPU-ağırlıklı işler thread pool içinde çalıştırılır.

---

## Frontend (Next.js App Router) Yapısı

Frontend kök dizini: `frontend/`

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
- Docker Compose (PostgreSQL 16 + Redis 7 + backend + frontend)
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
- **Septum** (port 8000 + 3000) — Backend + Frontend tek container'da

Yerel Ollama instance'ı eklemek için:

```bash
docker compose --profile ollama up
```

### Seçenek B: Yerel geliştirme

#### 1. Ortak ön gereksinimler

- Python 3.10+
- Node.js 18+ (Next.js 16 için)
- ffmpeg (Whisper için)

#### 2. Backend kurulumu

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

Tüm yapılandırma ilk çalıştırmada kurulum sihirbazı tarafından yapılır. `backend/` dizininde şifreleme anahtarları ve altyapı ayarlarını içeren bir `config.json` dosyası otomatik oluşturulur. Manuel yapılandırma dosyasına gerek yoktur.

Ardından backend'i başlatın:

```bash
uvicorn app.main:app --reload
```

#### 3. Frontend kurulumu

```bash
cd frontend
npm install
npm run dev
```

Varsayılan olarak:
- Backend: `http://localhost:8000`
- Frontend: `http://localhost:3000`

`src/lib/api.ts` içindeki backend base URL'inin kendi ortamınızla uyumlu olduğundan emin olun.

**İlk açılış:** Arayüz, uygulama ayarlarında kurulum tamamlanana kadar kısa bir kurulum sihirbazı (LLM sağlayıcı ve bağlantı testi) gösterir. Sohbetler sunucuda saklanır (`/api/chat-sessions`) ve sohbet kenar çubuğundan değiştirilebilir.

---

## Testleri Çalıştırma

Projede Septum içinde tanımlı özel bir `/test` kuralı bulunur:

- Değişen dosyaya göre ilgili pytest dosyası çalıştırılır. Örneğin:
  - `sanitizer.py` → `tests/test_sanitizer.py`
  - `anonymization_map.py` → `tests/test_anonymization_map.py`
  - `app/services/national_ids/*` → `tests/test_national_ids.py`
  - `app/services/ingestion/*` → `tests/test_ingesters.py`
  - vb.
- Eşleşme bulunamazsa tüm test paketi çalıştırılır.

**Sürekli entegrasyon:** GitHub Actions her push ve pull request'te arka uç testleri ile birlikte Ruff ve Bandit, `pip-audit` ve ön uç Jest, `tsc --noEmit` ile `npm audit` işlerini paralel çalıştırır.

Testleri manuel olarak çalıştırmak için:

```bash
cd backend
pytest tests/ -v
```

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

Tam OpenAPI şeması, backend çalışırken `http://localhost:8000/docs` adresinde mevcuttur.

---

## Yol Haritası / Genişletme

- Yeni ülke regülasyonları için recognizer registry tarafında yeni regulation pack'ler eklenebilir.
- Yeni ulusal kimlik formatları için national ID modülünde yeni validator ve recognizer eklenebilir.
- Yeni doküman formatları için ingestion katmanında özel ingester implementasyonları eklenebilir.
- NER model haritası, Settings → NER Models ekranından kullanıcı tarafından güncellenebilir.
- Yerel LLM (Ollama) ile zamir coreference çözümleme, dolaylı kişi referanslarını tespit eder.
- PII tespit kalite metrikleri ile tespit kapsamının veri odaklı değerlendirmesi.
