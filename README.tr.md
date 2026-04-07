<p align="center">
  <img src="septum_logo.png" alt="Septum logosu" width="220" />
</p>

<h3 align="center">Veriniz dışarı çıkmaz. Yapay zekanız çalışmaya devam eder.</h3>

<p align="center">
  <img src="https://img.shields.io/badge/backend-FastAPI-blue" alt="Backend: FastAPI" />
  <img src="https://img.shields.io/badge/frontend-Next.js%2016-black" alt="Frontend: Next.js 16" />
  <img src="https://img.shields.io/badge/testler-pytest-informational" alt="Testler: pytest" />
  <img src="https://img.shields.io/badge/odak-Gizlilik--Öncelikli-green" alt="Odak: Gizlilik-Öncelikli" />
  <a href="README.md">
    <img src="https://img.shields.io/badge/lang-EN-blue" alt="English README" />
  </a>
  <br />
  <a href="https://hub.docker.com/r/byerlikaya/septum">
    <img src="https://img.shields.io/docker/pulls/byerlikaya/septum?label=docker%20pulls" alt="Docker Pulls" />
  </a>
  <img src="https://img.shields.io/badge/guvenlik_taramasi-passing_(2026--03--10)-brightgreen" alt="Güvenlik taraması: passing (2026-03-10)" />
  <img src="https://img.shields.io/badge/bagimliliklar-denetim_temiz-brightgreen" alt="Bağımlılıklar: denetim temiz" />
</p>

<p align="center">
  <a href="#bu-kimin-için"><strong>Bu Kimin İçin?</strong></a>
  &middot;
  <a href="#hızlı-başlangıç"><strong>Hızlı Başlangıç</strong></a>
  &middot;
  <a href="ARCHITECTURE.tr.md"><strong>Mimari</strong></a>
  &middot;
  <a href="CHANGELOG.md"><strong>Değişiklik Günlüğü</strong></a>
  &middot;
  <a href="LICENSE"><strong>Lisans</strong></a>
</p>

---

## Septum Nedir?

Septum, dokümanlarınız ile bulut LLM'ler arasında duran bir **gizlilik odaklı AI ara katmanıdır**. ChatGPT, Claude veya herhangi bir LLM ile hassas şirket verilerinizi sorgulamanızı sağlar — **kişisel verileri otomatik tespit edip maskeleyerek buluta göndermeden önce korur**.

1. Dokümanlarınızı (PDF, Word, Excel, görsel, ses vb.) yüklersiniz.
2. Septum tüm kişisel verileri **yerelde tespit edip maskeler**.
3. LLM'e yalnızca anonimleştirilmiş metin gönderilir.
4. Cevap, gerçek isim ve değerlerle **yerelde** geri birleştirilir.

> **Tek cümleyle:** Septum, LLM gücünü kullanırken kişisel veri sızdırmak istemeyen ekipler için bir güvenlik katmanıdır.

**Önce ve sonra — LLM'in gerçekte gördüğü:**

```
Girdi:    "Ahmet Yılmaz Berlin'de yaşıyor, e-posta ahmet.yilmaz@corp.de, TC 12345678901"
Maskeli:  "[PERSON_1] [LOCATION_1]'de yaşıyor, e-posta [EMAIL_1], TC [NATIONAL_ID_1]"
```

LLM placeholder'larla cevap verir. Septum, cevabı size göstermeden önce gerçek değerleri yerelde geri yükler.

---

## Bu Kimin İçin?

- **Geliştiriciler** — gerçek müşteri verisiyle çalışan yapay zeka uygulamaları geliştiren
- **Ekipler** — GDPR, KVKK, HIPAA veya diğer gizlilik regülasyonlarına tabi olan
- **Şirketler** — iç dokümanlarla (sözleşmeler, İK dosyaları, sağlık kayıtları) LLM kullanan
- **Self-hosting savunucuları** — tam kontrol isteyen, verilerin altyapısından çıkmasını istemeyen

---

## Hangi Sorunları Çözer?

**Güvenli kurumsal doküman sorgulama** — Sözleşmeleri, müşteri dosyalarını, sağlık kayıtlarını veya İK dokümanlarını LLM ile sorgulayın. LLM yalnızca `[PERSON_1]`, `[EMAIL_2]` gibi maskeler görür, gerçek kimlikleri asla görmez.

**Regülasyon uyumluluğu** — GDPR, KVKK, HIPAA, CCPA ve diğer regülasyon risklerini, verileri buluta göndermeden **önce** anonimleştirerek azaltır. 17 hazır regülasyon paketi, en kısıtlayıcı kural her zaman kazanır.

**İç bilgi asistanı** — Dokümanlarınızı vektör veritabanına (RAG) gömerek şirket bilgisi üzerinde güçlü arama ve soru-cevap deneyimi oluşturur.

---

## Nasıl Çalışır?

1. **Dokümanlarınızı yükleyin**
   Dokümanlar sayfasından veya sohbet kenar çubuğundan PDF, Office, görsel veya ses dosyalarını ekleyin.

2. **Septum yerelde anonimleştirir**
   Dosya tipi, dil ve kişisel verileri otomatik tespit eder. Tüm PII'yi maskeler ve arama için hazırlar.

3. **Sorular sorun**
   *"Bu sözleşmedeki fesih koşulları neler?"*
   *"Bu müşterinin hangi ürünleri var?"*
   *"Son 6 aydaki vaka dosyalarını özetle."*

4. **Göndermeden önce onaylayın**
   LLM'e gönderilecek anonimleştirilmiş içeriği tam olarak görün. Onaylayın veya reddedin.

5. **Gerçek değerlerle cevap alın**
   Septum placeholder'ları yerelde orijinal değerlere geri çevirir ve size doğal, okunabilir bir cevap sunar.

---

## Temel Özellikler

- **Yerel PII Koruması** — Kişisel verileri buluta göndermeden önce tespit edip maskeler. Dosyalar şifreli saklanır (AES-256-GCM). **Onay Mekanizması** her LLM çağrısından önce maskelenmiş çıktıyı incelemenizi sağlar — onayınız olmadan hiçbir şey gönderilmez.
- **Çoklu Regülasyon Desteği** — 17 hazır paket (GDPR, KVKK, CCPA, HIPAA, LGPD, PIPEDA, PDPA, APPI, PIPL, POPIA, DPDP, UK GDPR ve daha fazlası). Aynı anda birden fazla aktif; en kısıtlayıcı kazanır.
- **Onay Mekanizması** — LLM'e gönderilmeden önce neyin paylaşılacağını görün ve onaylayın.
- **Özel Kurallar** — Kendi kalıplarınızı tanımlayın: regex, anahtar kelime listeleri veya LLM-tabanlı tespit.
- **Zengin Format Desteği** — PDF, Office, hesap tabloları, görseller (OCR), ses (Whisper transkripsiyon), e-postalar.
- **Hibrit Arama** — BM25 kelime eşleme + FAISS semantik arama, Reciprocal Rank Fusion ile birleştirilir.
- **Yapısal Veri Çıkarımı** — PDF'lerden tabloları ve anahtar-değer çiftlerini otomatik tespit eder.
- **Denetim Kaydı** — Salt-ekleme uyumluluk günlüğü ve varlık tespit metrikleri. Denetim olaylarında ham PII bulunmaz.
- **Çoklu Sağlayıcı** — Anthropic, OpenAI, OpenRouter ve yerel Ollama ile çalışır. Arayüzden değiştirin.
- **JWT Kimlik Doğrulama ve RBAC** — Kullanıcı rolleri (admin/editor/viewer) ile doküman ve oturum kapsamı.

---

## Neden Septum?

| Özellik | Septum | ChatGPT / Claude (düz) | Azure Presidio (bağımsız) | Özel LangChain pipeline |
|---|:---:|:---:|:---:|:---:|
| PII buluta gitmeden maskelenir | **Evet** | Hayır | Yalnızca tespit | Kendin yap |
| Çoklu regülasyon (17 paket) | **Evet** | Hayır | Hayır | Kendin yap |
| LLM öncesi onay mekanizması | **Evet** | Hayır | Hayır | Kendin yap |
| De-anonimleştirme (cevaplarda gerçek değerler) | **Evet** | N/A | Hayır | Kendin yap |
| Hibrit arama ile doküman RAG | **Evet** | Hayır | Hayır | Kısmi |
| Özel tespit kuralları (regex, anahtar kelime, LLM) | **Evet** | Hayır | Sınırlı | Kendin yap |
| Kullanıma hazır web arayüzü | **Evet** | N/A | Hayır | Hayır |
| Denetim kaydı ve uyumluluk raporlama | **Evet** | Hayır | Hayır | Kendin yap |
| Herhangi bir LLM sağlayıcı ile çalışır | **Evet** | Tek sağlayıcı | Yalnızca Azure | Yapılandırılabilir |
| Tamamen self-hosted, veri dışarı çıkmaz | **Evet** | Hayır | Bulut servisi | Duruma bağlı |

**Temel fark:** Diğer araçlar bulmacının parçalarını sunar — burada tespit, orada bir vektör veritabanı. Septum **uçtan uca komple pipeline'dır**: tespit → anonimleştirme → eşleme → arama → onay → LLM çağrısı → de-anonimleştirme → denetim. Kutudan çıktığı gibi, arayüzüyle, her regülasyon için.

---

## Tespit ve Gizlilik

Septum, hem yanlış negatifleri (kaçan PII) hem de yanlış pozitifleri (gereksiz maskeleme) en aza indirmek için **çok katmanlı PII tespit pipeline'ı** kullanır. Her katman tespit kapasitesi ekler; hepsi **yerelde** çalışır.

### Her Katman Neyi Tespit Eder?

| Katman | Teknoloji | Tespit Edilen Varlık Tipleri |
|:---:|-----------|-------------|
| 1 | **Presidio** — regex desenleri + algoritmik doğrulayıcılar (Luhn, IBAN MOD-97, TCKN, CPF, SSN checksum'ları). Çok dilli bağlam anahtar kelimeleri ile context-aware tanıma. | EMAIL_ADDRESS, PHONE_NUMBER, IP_ADDRESS, CREDIT_CARD_NUMBER, IBAN, NATIONAL_ID, MEDICAL_RECORD_NUMBER, HEALTH_INSURANCE_ID, POSTAL_ADDRESS, DATE_OF_BIRTH, MAC_ADDRESS, URL, COORDINATES, COOKIE_ID, DEVICE_ID, SOCIAL_SECURITY_NUMBER, CPF, PASSPORT_NUMBER, DRIVERS_LICENSE, TAX_ID, LICENSE_PLATE |
| 2 | **NER** — HuggingFace XLM-RoBERTa, dile özgü model seçimi (20+ dil). BÜYÜK HARF girdi otomatik başlık formatına dönüştürülür. | PERSON_NAME, LOCATION, ORGANIZATION_NAME |
| 3 | **Ollama** — bağlam duyarlı doğrulama, takma ad tespiti ve semantik varlık tespiti için yerel LLM | PERSON_NAME takma adları/lakapları; DIAGNOSIS, MEDICATION, RELIGION, POLITICAL_OPINION, SEXUAL_ORIENTATION, ETHNICITY, CLINICAL_NOTE, BIOMETRIC_ID, DNA_PROFILE |

Katmanlar kümülatiftir: K1 yapısal tanımlayıcıları ve bağlam etiketli değerleri (doğum tarihleri, pasaport numaraları, cihaz ID'leri vb.) yakalar, K2 transformer NER ile isimleri, konumları ve kuruluşları ekler (BÜYÜK HARF metin dahil), K3 yerel LLM ile semantik tipleri (tıbbi teşhisler, ilaçlar, dini/siyasi/etnik referanslar) ve takma adları tespit eder. Sonuçlar coreference çözümleme ile birleştirilir; böylece "Ahmet", "A. Yılmaz" ve "Bay Yılmaz" hepsi aynı `[PERSON_1]` placeholder'ına eşlenir.

> **Kıyaslama modelleri:** NER, `akdeniz27/xlm-roberta-base-turkish-ner` (TR) ve `Davlan/xlm-roberta-base-wikiann-ner` (diğer tüm diller) kullanır. Ollama katmanı `aya-expanse:8b` kullanır. Sonuçlar farklı Ollama modelleri ile değişebilir — daha büyük modeller genellikle semantik tespit doğruluğunu artırır.

### Kıyaslama Sonuçları

Tüm 17 yerleşik regülasyon aktif. 23 varlık tipinde **3,268 algoritmik olarak üretilmiş PII değeri** (geçerli Luhn, IBAN MOD-97, TCKN checksum'ları). Presidio tipi başına 150 örnek, 160 kişi ismi (karma büyük/küçük harf + BÜYÜK HARF, EN/TR), 100 konum (EN/TR), 30 kuruluş ismi (EN/TR) ve takma ad tespiti. Tam tekrarlanabilirlik için sabit seed.

<p align="center">
  <img src="screenshots/benchmark-f1-by-type.png" alt="Varlık Tipine Göre F1 Skoru" width="900" />
</p>

<p align="center">
  <img src="screenshots/benchmark-layer-comparison.png" alt="Katmana Göre Tespit Doğruluğu" width="700" />
</p>

| Katman | Varlıklar | Tipler | Precision | Recall | F1 |
|---|:---:|:---:|:---:|:---:|:---:|
| Presidio (K1) — desenler + doğrulayıcılar | 1,710 | 20 | %100 | %94,4 | %97,1 |
| NER (K2) — XLM-RoBERTa + BÜYÜK HARF normalizasyonu | 770 | 3 | %97,5 | %92,7 | %95,1 |
| Ollama (K3) — aya-expanse:8b | 788 | 3 | %99,7 | %91,6 | %95,5 |
| **Birleşik** | **3,268** | **23** | **%99,3** | **%93,3** | **%96,2** |

> NER (K2), tıbbi ve hukuki belgelerde yaygın olan BÜYÜK HARF isimleri otomatik başlık formatı normalizasyonuyla tespit eder ve kuruluş isimlerini tanır. Ollama (K3) adayları doğrular ve takma adları yakalar. Benchmark, gerçek dünya seviyelerine düşüren adversarial edge case'leri (boşluklu IBAN, noktalı telefon numaraları vb.) içerir. Tekrarlanabilir: `pytest tests/benchmark_detection.py -v -s`

### Tespit Kapsamı ve Sınırlamalar

**Hiçbir PII tespit sistemi %100 doğru değildir.** Septum'un benchmark'ı bu konuda şeffaftır:

- **Regülasyonlardaki 37 varlık tipinin tamamı artık tespit edilebilir** — 21'i Presidio pattern recognizer'ları, 3'ü NER, 9'u Ollama semantik tespit, 7'si üst tip kapsamı (örn. CITY → LOCATION, FIRST_NAME → PERSON_NAME) ile.
- **23 varlık tipi aktif olarak kıyaslanır** — 14 dilde 3.268 test değeri ve adversarial edge case'ler dahil.
- **Semantik tipler** (DIAGNOSIS, MEDICATION, RELIGION, POLITICAL_OPINION vb.) Ollama katmanı tarafından tespit edilir ve çalışan bir yerel LLM gerektirir. Tespit doğruluğu kullanılan modele bağlıdır (benchmark `aya-expanse:8b` kullanır).
- **Bağlam bağımlı tanıyıcılar** (DATE_OF_BIRTH, PASSPORT_NUMBER, SSN, TAX_ID vb.) false positive'leri azaltmak için değer yakınında bağlam anahtar kelimeleri gerektirir. 8+ dilde çok dilli anahtar kelimeler desteklenir.
- **Adversarial formatlar** (boşluklu TCKN, noktalı telefon numaraları) kontrollü format testlerinden daha düşük tespit oranları gösterir. Bu dürüstçe raporlanır.

**Onay Mekanizması güvenlik ağınızdır.** LLM'e metin gönderilmeden önce neyin iletileceğini tam olarak görür ve reddedebilirsiniz. Bu tasarım gereğidir — otomatik tespit riski azaltır, insan incelemesi ortadan kaldırır.

Pipeline detayları için bkz. [Mimari — PII Tespiti ve Anonimleştirme Akışı](ARCHITECTURE.tr.md#pii-tespiti-ve-anonimleştirme-akışı).

---

<!-- Ekran görüntüleri / demo GIF buraya eklenecek -->

---

## Hızlı Başlangıç

### Docker (önerilen)

```bash
docker pull byerlikaya/septum
docker run --name septum -p 3000:3000 -p 8000:8000 \
  -v septum-data:/app/data \
  -v septum-uploads:/app/uploads \
  -v septum-anon-maps:/app/anon_maps \
  -v septum-vector-indexes:/app/vector_indexes \
  -v septum-bm25-indexes:/app/bm25_indexes \
  byerlikaya/septum
```

**http://localhost:3000** adresini açın — kurulum sihirbazı her şeyi adım adım yapılandırır:

1. **Veritabanı** — SQLite (varsayılan, sıfır konfigürasyon) veya PostgreSQL
2. **Önbellek** — In-memory (varsayılan) veya Redis
3. **LLM Provider** — Anthropic, OpenAI, OpenRouter veya Ollama (lokal)
4. **Ses modeli** — Whisper model seçimi (opsiyonel)

`.env` dosyası yok, manuel konfigürasyon yok. Veriler Docker volume'lar aracılığıyla otomatik korunur.

### Güncelleme

```bash
docker stop septum && docker rm septum
docker pull byerlikaya/septum
docker run --name septum -p 3000:3000 -p 8000:8000 \
  -v septum-data:/app/data \
  -v septum-uploads:/app/uploads \
  -v septum-anon-maps:/app/anon_maps \
  -v septum-vector-indexes:/app/vector_indexes \
  -v septum-bm25-indexes:/app/bm25_indexes \
  byerlikaya/septum
```

`docker pull` adımı gereklidir — `docker run` tek başına önbellekteki eski image'ı kullanır. Verileriniz named volume'larda korunur.

### Docker Compose (PostgreSQL + Redis)

```bash
docker compose up
```

PostgreSQL, Redis ve Septum'u tek komutla başlatır. Yerel Ollama için `--profile ollama` ekleyin. Kurulum sihirbazı ilk ziyarette LLM provider'ı yapılandırır.

### Yerel Geliştirme

```bash
./dev.sh --setup   # İlk kurulum: bağımlılıkları yükle
./dev.sh           # Backend (port 8000) + frontend (port 3000) başlat
```

İlk ziyarette kurulum sihirbazı açılır.

Mimari detaylar için bkz. **[ARCHITECTURE.tr.md](ARCHITECTURE.tr.md)**.

---

## Geliştiriciler İçin

### Hızlı API Örneği

```bash
# Doküman yükle
curl -X POST http://localhost:8000/api/documents/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@contract.pdf"

# Soru sor (SSE ile akışlı yanıt)
curl -N -X POST http://localhost:8000/api/chat/ask \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Fesih koşulları neler?", "document_id": 1}'
```

Chat endpoint'i Server-Sent Events döner: `meta` (oturum bilgisi) → `approval_required` (onay için maskelenmiş parçalar) → `answer_chunk` (akışlı yanıt) → `end`.

Septum aradaki her şeyi yönetir: PII tespiti, anonimleştirme, arama, LLM çağrısı ve de-anonimleştirme. Uygulamanız sadece soru gönderir ve temiz cevaplar alır.

Tam API referansı, pipeline detayları, kod yapısı ve dağıtım seçenekleri için bkz. **[ARCHITECTURE.tr.md](ARCHITECTURE.tr.md)**.

---

## Lisans

Detaylar için [LICENSE](LICENSE) dosyasına bakın.
