<p align="center">
  <img src="septum_logo.png" alt="Septum logosu" width="220" />
</p>

<h3 align="center">Kişisel veri sızdırmadan, şirket verinizle yapay zekâ kullanın.</h3>

<p align="center">
  <img src="https://img.shields.io/badge/backend-FastAPI-blue" alt="Backend: FastAPI" />
  <img src="https://img.shields.io/badge/frontend-Next.js%2016-black" alt="Frontend: Next.js 16" />
  <img src="https://img.shields.io/badge/testler-pytest-informational" alt="Testler: pytest" />
  <img src="https://img.shields.io/badge/odak-Gizlilik--Öncelikli-green" alt="Odak: Gizlilik-Öncelikli" />
  <a href="README.md">
    <img src="https://img.shields.io/badge/lang-EN-blue" alt="English README" />
  </a>
  <br />
  <img src="https://img.shields.io/badge/guvenlik_taramasi-passing_(2026--03--10)-brightgreen" alt="Güvenlik taraması: passing (2026-03-10)" />
  <img src="https://img.shields.io/badge/bagimliliklar-denetim_temiz-brightgreen" alt="Bağımlılıklar: denetim temiz" />
</p>

<p align="center">
  <a href="#ekran-görüntüleri"><strong>Ekran Görüntüleri</strong></a>
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

Septum, dokümanlarınız ile bulut LLM'ler arasında duran bir **gizlilik odaklı AI ara katmanıdır**. ChatGPT, Claude veya herhangi bir LLM ile hassas şirket verilerinizi sorgulamanızı sağlar — **ham kişisel veriler asla makinenizi terk etmeden**.

1. Dokümanlarınızı (PDF, Word, Excel, görsel, ses vb.) yüklersiniz.
2. Septum tüm kişisel verileri **yerelde tespit edip maskeler**.
3. LLM'e yalnızca anonimleştirilmiş metin gönderilir.
4. Cevap, gerçek isim ve değerlerle **yerelde** geri birleştirilir.

> **Tek cümleyle:** Septum, LLM gücünü kullanırken kişisel veri sızdırmak istemeyen ekipler için bir güvenlik katmanıdır.

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

- **Yerel PII Koruması** — Ham kişisel veri asla makinenizi terk etmez. Dosyalar şifreli saklanır (AES-256-GCM).
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

Septum, hem yanlış negatifleri (kaçan PII) hem de yanlış pozitifleri (gereksiz maskeleme) en aza indirmek için **3 katmanlı PII tespit pipeline'ı** kullanır:

| Katman | Teknoloji | Amaç |
|--------|----------|-------|
| 1 | Microsoft Presidio + regülasyona özel tanıyıcı paketleri | Ülke-spesifik checksum doğrulayıcılarıyla desen tabanlı tespit |
| 2 | HuggingFace NER (XLM-RoBERTa), dile duyarlı model seçimi | 20+ dilde AI tabanlı isim ve varlık tespiti |
| 3 | Yerel Ollama LLM (isteğe bağlı) | Bağlam duyarlı takma ad ve lakap tespiti |

Tüm katmanlar **yerelde** çalışır. Sonuçlar coreference çözümleme ile birleştirilir; böylece "Ahmet", "A. Yılmaz" ve "Bay Yılmaz" hepsi aynı `[PERSON_1]` placeholder'ına eşlenir.

> Varlık tipleri ve regülasyonlar bazında resmi doğruluk kıyaslamaları hazırlanmaktadır ve burada yayınlanacaktır.

Pipeline detayları için bkz. [Mimari — PII Tespiti ve Anonimleştirme Akışı](ARCHITECTURE.tr.md#pii-tespiti-ve-anonimleştirme-akışı).

---

## Ekran Görüntüleri

**1. Sohbet — soru sorun, göndermeden önce onaylayın**

<p align="center">
  <img src="screenshots/1-chat.png" alt="Onay adımlı sohbet ekranı" width="900" />
</p>

**2. Dokümanlar — yükleyin ve yönetin**

<p align="center">
  <img src="screenshots/2-documents.png" alt="Doküman listesi ve yükleme ekranı" width="900" />
</p>

**3. Regülasyonlar — 17 hazır paket, özel kurallar**

<p align="center">
  <img src="screenshots/11-regulations.png" alt="Regülasyon ruleset yönetimi ekranı" width="900" />
</p>

**4. Ayarlar — LLM, gizlilik, RAG yapılandırması**

<p align="center">
  <img src="screenshots/4-cloudllm.png" alt="Bulut LLM yapılandırma ayarları" width="900" />
</p>

<details>
<summary><strong>Daha fazla ekran görüntüsü</strong></summary>

**Gizlilik ve anonimleştirme katmanları**
<p align="center">
  <img src="screenshots/5-privacySanitization.png" alt="Gizlilik ve anonimleştirme ayarları" width="900" />
</p>

**Yerel model yapılandırması**
<p align="center">
  <img src="screenshots/6-localmodels.png" alt="Yerel model ayarları" width="900" />
</p>

**RAG yapılandırması**
<p align="center">
  <img src="screenshots/7-rag.png" alt="RAG yapılandırma ayarları" width="900" />
</p>

**Ingestion / içe aktarma ayarları**
<p align="center">
  <img src="screenshots/8-ingestion.png" alt="Ingestion, OCR ve transkripsiyon ayarları" width="900" />
</p>

**Metin normalizasyon kuralları**
<p align="center">
  <img src="screenshots/9-textNormalizationRules.png" alt="Metin normalizasyon kuralı yapılandırması" width="900" />
</p>

**NER model eşleştirmeleri**
<p align="center">
  <img src="screenshots/10-NERModels.png" alt="Dil → NER modeli eşleştirme ayarları" width="900" />
</p>

</details>

---

## Hızlı Başlangıç

### Docker Compose (önerilen)

```bash
cp .env.example .env
# .env dosyasını düzenleyin — en az bir LLM API anahtarı ayarlayın (ANTHROPIC_API_KEY veya OPENAI_API_KEY)
docker compose up
```

`http://localhost:3000` adresini açın. Kurulum sihirbazı ilk yapılandırma adımlarında size rehberlik eder.

Yerel Ollama instance'ı eklemek için:

```bash
docker compose --profile ollama up
```

### Yerel Geliştirme

```bash
# Backend
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # API anahtarınızı girin
uvicorn app.main:app --reload

# Frontend (ayrı terminalde)
cd frontend && npm install && npm run dev
```

Tüm kurulum seçenekleri (Docker, yerel geliştirme, ortam değişkenleri) için bkz. [Mimari — Kurulum](ARCHITECTURE.tr.md#kurulum).

---

## Geliştiriciler İçin

Septum'un iç yapısı — PII pipeline detayları, kod yapısı, API referansı, teknoloji yığını ve dağıtım seçenekleri — **[ARCHITECTURE.tr.md](ARCHITECTURE.tr.md)** dosyasında belgelenmiştir.

---

## Lisans

Detaylar için [LICENSE](LICENSE) dosyasına bakın.
