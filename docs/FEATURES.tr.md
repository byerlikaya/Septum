# Septum — Özellik ve Tespit Referansı

<p align="center">
  <a href="../README.tr.md"><strong>🏠 Ana Sayfa</strong></a>
  &nbsp;·&nbsp;
  <strong>✨ Özellikler</strong>
  &nbsp;·&nbsp;
  <a href="BENCHMARK.tr.md"><strong>📈 Benchmark</strong></a>
  &nbsp;·&nbsp;
  <a href="ARCHITECTURE.tr.md"><strong>🏗️ Mimari</strong></a>
  &nbsp;·&nbsp;
  <a href="DOCUMENT_INGESTION.tr.md"><strong>📊 Doküman İşleme</strong></a>
  &nbsp;·&nbsp;
  <a href="SCREENSHOTS.tr.md"><strong>📸 Ekran Görüntüleri</strong></a>
  &nbsp;·&nbsp;
  <a href="../CONTRIBUTING.tr.md"><strong>🤝 Katkı</strong></a>
  &nbsp;·&nbsp;
  <a href="../CHANGELOG.md"><strong>📝 Changelog</strong></a>
</p>

---

## İçindekiler

- [Tespit Hattı](#tespit-hattı)
- [Regülasyon Paketleri](#regülasyon-paketleri)
- [Otomatik RAG Yönlendirme](#otomatik-rag-yönlendirme)
- [Neden Septum](#neden-septum)
- [MCP Entegrasyonu](#mcp-entegrasyonu)
- [REST API ve Kimlik Doğrulama](#rest-api-ve-kimlik-doğrulama)

---

## Tespit Hattı

Septum, üç katmanlı tespit hattını tamamen yerelde çalıştırır. Her katman bir öncekinin üstüne bilgi ekler; bulguların tamamı son adımda bir coreference çözümleyicisinden geçer. Böylece aynı kişi metinde farklı biçimlerde anılsa dahi tek bir `[PERSON_1]` placeholder'ı ile temsil edilir.

<p align="center">
  <a href="#tespit-hattı"><img src="../assets/detection-pipeline.tr.svg" alt="Septum üç katmanlı tespit hattı — Presidio, NER, Ollama ve coreference birleşimi" width="1100" /></a>
</p>

| Katman | Teknoloji | Tespit ettiği varlık tipleri |
|:---:|:---|:---|
| 1 | **Presidio** — algoritmik doğrulayıcılarla güçlendirilmiş regex desenleri (Luhn, IBAN MOD-97, TCKN, CPF, SSN). Çok dilli anahtar kelime listeleriyle desteklenen bağlama duyarlı tanıyıcılar. | EMAIL_ADDRESS, PHONE_NUMBER, IP_ADDRESS, CREDIT_CARD_NUMBER, IBAN, NATIONAL_ID, MEDICAL_RECORD_NUMBER, HEALTH_INSURANCE_ID, POSTAL_ADDRESS, DATE_OF_BIRTH, MAC_ADDRESS, URL, COORDINATES, COOKIE_ID, DEVICE_ID, SOCIAL_SECURITY_NUMBER, CPF, PASSPORT_NUMBER, DRIVERS_LICENSE, TAX_ID, LICENSE_PLATE |
| 2 | **NER** — dile göre model seçen HuggingFace XLM-RoBERTa (20+ dil). BÜYÜK HARF girdi, çıkarım öncesinde otomatik olarak başlık harflerine çevrilir. LOCATION ve ORGANIZATION_NAME çıktıları, ortak isimlerden kaynaklanan yalancı pozitifleri ayıklayan "çok kelimeli veya yüksek skor" kapısından geçer. | PERSON_NAME, LOCATION, ORGANIZATION_NAME |
| 3 | **Ollama** — bağlam doğrulama, takma ad tespiti ve semantik varlıklar için yerel LLM. | PERSON_NAME takma adları; DIAGNOSIS, MEDICATION, RELIGION, POLITICAL_OPINION, SEXUAL_ORIENTATION, ETHNICITY, CLINICAL_NOTE, BIOMETRIC_ID, DNA_PROFILE |

**Coreference çözümleme.** Üç katman span'lerini ürettikten sonra sanitizer, aynı kişiye yapılan tüm atıfları tek bir placeholder altında toplar. Aynı dokümandaki `"John"`, `"J. Doe"` ve `"Mr. Doe"` ifadelerinin tamamı tek bir `[PERSON_1]`'e indirgenir. Çözümleme yalnızca cümleler arasında değil, aynı dokümanın farklı parçaları arasında da çalışır.

**3. katman isteğe bağlıdır.** Ayarlardan `use_ollama_semantic_layer=false` yaparak devre dışı bırakabilirsiniz. 1. ve 2. katmanlar yapısal kimlikleri ve isimleri yakalar; 3. katman ise regex ve NER'in göremediği hassas kategorileri (sağlık, din, siyasi görüş vb.) tespit eder. Doğruluk, seçilen Ollama modeline bağlıdır; Septum varsayılan olarak `aya-expanse:8b` kullanır.

---

## Regülasyon Paketleri

Septum 17 hazır regülasyon paketiyle gelir. Birden fazlası aynı anda aktif olabilir — sanitizer kuralların birleşimini uygular ve çakışma durumunda en kısıtlayıcı kural geçerli olur.

| Bölge | Kod | Regülasyon |
|:---|:---|:---|
| 🇪🇺 AB / AEA | `gdpr` | General Data Protection Regulation |
| 🇺🇸 ABD (Sağlık) | `hipaa` | Health Insurance Portability and Accountability Act |
| 🇹🇷 Türkiye | `kvkk` | 6698 sayılı Kişisel Verilerin Korunması Kanunu |
| 🇧🇷 Brezilya | `lgpd` | Lei Geral de Proteção de Dados |
| 🇺🇸 ABD (Kaliforniya) | `ccpa` | California Consumer Privacy Act |
| 🇺🇸 ABD (Kaliforniya) | `cpra` | California Privacy Rights Act |
| 🇬🇧 Birleşik Krallık | `uk_gdpr` | UK GDPR |
| 🇨🇦 Kanada | `pipeda` | Personal Information Protection and Electronic Documents Act |
| 🇹🇭 Tayland | `pdpa_th` | Personal Data Protection Act |
| 🇸🇬 Singapur | `pdpa_sg` | Personal Data Protection Act |
| 🇯🇵 Japonya | `appi` | Act on the Protection of Personal Information |
| 🇨🇳 Çin | `pipl` | Personal Information Protection Law |
| 🇿🇦 Güney Afrika | `popia` | Protection of Personal Information Act |
| 🇮🇳 Hindistan | `dpdp` | Digital Personal Data Protection Act |
| 🇸🇦 Suudi Arabistan | `pdpl_sa` | Personal Data Protection Law |
| 🇳🇿 Yeni Zelanda | `nzpa` | Privacy Act 2020 |
| 🇦🇺 Avustralya | `australia_pa` | Privacy Act 1988 |

Her satır, `packages/core/septum_core/recognizers/` altında yüklenebilir bir pakettir. Her varlık tipinin hukuki kaynağı ise [hukuki kaynaklar belgesinde](../packages/core/docs/REGULATION_ENTITY_SOURCES.md) listelidir.

**Bölgeye özgü kimlik numarası doğrulayıcıları** sadece örüntüye değil, algoritmaya dayanır: TCKN (Türkiye, mod-10 + mod-11 checksum), Aadhaar (Hindistan, Verhoeff), CPF (Brezilya, iki basamaklı checksum), NRIC/FIN (Singapur, harf checksum'ı), Resident ID (Çin, ISO 7064 MOD 11-2), NINO (İngiltere), CNPJ (Brezilya), My Number (Japonya) ve diğerleri. Geçersiz checksum reddedilir; rastgele 11 haneli bir dize yalancı pozitif üretmez.

**Özel kurallar.** Dashboard üzerinden adminler regex, anahtar kelime ya da LLM promptu tabanlı özel kural setleri tanımlayabilir. Özel kurallar hazır paketlerle yan yana çalışır; kural birleştirme mantığı aynen korunur.

---

## Otomatik RAG Yönlendirme

Sohbet kenar çubuğunda doküman seçilmediğinde Septum, doküman aramak ile doğrudan sohbet yoluyla yanıt vermek arasındaki kararı kendisi verir.

<p align="center">
  <a href="#otomatik-rag-yönlendirme"><img src="../assets/auto-rag-routing.tr.svg" alt="Otomatik RAG yönlendirme — SEARCH/CHAT sınıflandırıcısı, relevans eşiği, düz LLM ve onay kapısı" width="820" /></a>
</p>

Üç yol oluşur:

1. **Manuel RAG** — kullanıcı açıkça doküman seçer. Sınıflandırıcı atlanır; retrieval seçilen dokümanlarda çalışır.
2. **Otomatik RAG** — seçim yok, sınıflandırıcı `SEARCH` diyor ve relevans skoru eşiğin üzerinde. Kullanıcının tüm dokümanlarından parçalar getirilir.
3. **Düz LLM** — seçim yok, sınıflandırıcı `CHAT` diyor ya da relevans eşiğin altında. Doküman bağlamı eklenmez; LLM serbestçe cevaplar.

SSE meta event'i `rag_mode: "manual" | "auto" | "none"` ve `matched_document_ids` alanlarını taşır; dashboard her asistan mesajında hangi yolun seçildiğini rozetle gösterir. Eşik değeri, RAG ayarlar sekmesinde `rag_relevance_threshold` olarak tutulur (varsayılan 0,35).

---

## Neden Septum

| Yetenek | Septum | Düz ChatGPT / Claude | Azure Presidio | LangChain Pipeline |
|:---|:---:|:---:|:---:|:---:|
| Buluta gitmeden önce PII maskeleme | **Evet** | Hayır | Sadece tespit | Elle geliştirilir |
| Çoklu regülasyon (17 paket) | **Evet** | Hayır | Hayır | Elle geliştirilir |
| LLM öncesi onay kapısı | **Evet** | Hayır | Hayır | Elle geliştirilir |
| Placeholder geri yazma (gerçek değerler) | **Evet** | Yok | Hayır | Elle geliştirilir |
| Hibrit retrieval ile doküman RAG | **Evet** | Hayır | Hayır | Kısmen |
| Otomatik RAG niyet yönlendirme | **Evet** | Hayır | Hayır | Elle geliştirilir |
| Özel tespit kuralları | **Evet** | Hayır | Sınırlı | Elle geliştirilir |
| Hazır web arayüzü | **Evet** | Yok | Hayır | Hayır |
| Denetim kaydı ve uyumluluk | **Evet** | Hayır | Hayır | Elle geliştirilir |
| Herhangi bir LLM sağlayıcısı | **Evet** | Tek | Sadece Azure | Yapılandırılabilir |
| Tamamen self-hosted | **Evet** | Hayır | Bulut servisi | Duruma bağlı |

Diğer araçlar bulmacanın yalnızca parçalarını sunar — bir yerde tespit, başka bir yerde vektör deposu. Septum ise uçtan uca bütün bir hat olarak gelir: tespit → anonimleştirme → eşleme → retrieval → onay → LLM çağrısı → placeholder geri yazma → denetim. Kurulumdan hemen sonra kullanıma hazır, arayüzüyle birlikte ve herhangi bir regülasyon için.

---

## MCP Entegrasyonu

Septum, aynı yerel PII maskeleme hattını MCP uyumlu her istemciye bağlayan bağımsız bir **Model Context Protocol** sunucusuyla ([`septum-mcp`](../packages/mcp/)) birlikte gelir. MCP açık ve sağlayıcıdan bağımsız bir [spesifikasyondur](https://modelcontextprotocol.io); sunucu üç standart taşımanın üçünü de destekler:

- **stdio** (varsayılan) — alt-süreç olarak başlatılan istemciler için: Claude Desktop, Cursor, Windsurf, ChatGPT Desktop, Zed ve Python / TypeScript / Rust / Go / C# / Java SDK'leriyle yazılmış her araç.
- **streamable-http** — uzak, tarayıcı ya da container içi istemciler için modern HTTP taşıması. `Authorization: Bearer <SEPTUM_MCP_HTTP_TOKEN>` üzerinden bearer token kimlik doğrulaması.
- **sse** — streamable-http'ye henüz geçmemiş istemciler için tutulan legacy HTTP + Server-Sent Events taşıması.

`septum-core` süreç içinde çalışır; ham PII ağa hiç erişmez.

**Sunulan araçlar:**

| Araç | Amaç |
|:---|:---|
| `mask_text` | Bir metindeki PII'yi maskeler ve bir session id döndürür. |
| `unmask_response` | LLM yanıtındaki orijinal değerleri session id ile geri yazar. |
| `detect_pii` | Salt-okunur tarama — session tutmadan varlıkları listeler. |
| `scan_file` | Yerel dosyayı (`.txt`, `.md`, `.csv`, `.json`, `.pdf`, `.docx`) okuyup tarar. |
| `list_regulations` | 17 hazır regülasyon paketini ve varlık tiplerini listeler. |
| `get_session_map` | `{orijinal → placeholder}` eşlemesini yalnızca yerel hata ayıklama için döndürür. |

**Stdio istemcisi** (Claude Desktop, Cursor, Windsurf, Zed, ChatGPT Desktop):

```json
{
  "mcpServers": {
    "septum": {
      "command": "septum-mcp",
      "env": {
        "SEPTUM_REGULATIONS": "gdpr,kvkk",
        "SEPTUM_LANGUAGE": "tr"
      }
    }
  }
}
```

**HTTP istemcisi** (uzak ajan, tarayıcı uzantısı, paylaşılan takım sunucusu):

```json
{
  "mcpServers": {
    "septum": {
      "url": "https://mcp.example.com/mcp",
      "headers": {
        "Authorization": "Bearer <token>"
      }
    }
  }
}
```

HTTP sunucusunu kendiniz çalıştırmak için:

```bash
SEPTUM_MCP_HTTP_TOKEN=$(openssl rand -hex 32) \
  septum-mcp --transport streamable-http --host 0.0.0.0 --port 8765
```

Tam HTTP deployment kılavuzu (Docker, compose profilleri, TLS reverse-proxy şablonu), ortam değişkeni referansı ve uçtan uca kullanım örnekleri için [MCP sunucu kılavuzuna](../packages/mcp/README.md) bakın.

---

## REST API ve Kimlik Doğrulama

Septum backend'i, `/docs` (Swagger) ve `/redoc` altında belgelenen bir FastAPI REST katmanı sunar. İki kimlik doğrulama yöntemi desteklenir.

### JWT (tarayıcı oturumu, kısa ömürlü)

Kurulum sihirbazı ilk admin hesabını oluşturur; sonraki login'lerde 24 saat geçerli bir JWT döndürülür.

```bash
curl -X POST http://localhost:3000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email": "admin@example.com", "password": "sifreniz"}'
# → {"access_token": "...", "token_type": "bearer"}
```

### API anahtarları (CI/CD, MCP entegrasyonları, uzun ömürlü)

Adminler `POST /api/api-keys` ile programatik API anahtarı oluşturur. Ham anahtar yalnızca **bir kez** gösterilir; kalıcı olarak yalnızca 8 karakterlik önek ve SHA-256 hash'i tutulur.

```bash
# Anahtar oluştur (yanıt raw_key içerir — şimdi kaydedin, sonradan geri alamazsınız)
curl -X POST http://localhost:3000/api/api-keys \
  -H 'Authorization: Bearer <jwt>' \
  -H 'Content-Type: application/json' \
  -d '{"name": "ci-pipeline", "expires_at": null}'

# Sonraki tüm isteklerde kullanın
curl -H 'X-API-Key: sk-septum-<64 hex>' http://localhost:3000/api/auth/me

# Anahtarları listele (sadece önek ve metadata — ham anahtar bir daha dönmez)
curl -H 'X-API-Key: sk-septum-…' http://localhost:3000/api/api-keys

# İptal et
curl -X DELETE -H 'X-API-Key: sk-septum-…' http://localhost:3000/api/api-keys/{id}
```

### Rate limit

| Endpoint | Limit |
|:---|:---|
| `POST /api/auth/register` | dakikada 3 |
| `POST /api/auth/login` | dakikada 5 |
| `POST /api/api-keys` | dakikada 10 |
| Diğer hepsi | dakikada 60 (`RATE_LIMIT_DEFAULT` ile yapılandırılır) |

API anahtarıyla gelen isteklere IP yerine **anahtar öneki** bazında rate limit uygulanır; böylece paylaşılan NAT arkasındaki her servis kendi kotasına sahip olur. Anonim ve JWT isteklerde ise IP bazlı limit geçerlidir. Redis yapılandırıldığında limit sayaçları Redis'te tutulur, aksi hâlde süreç içi bellekte saklanır (bu mod yalnızca tek node'luk geliştirme senaryolarına uygundur).

### Hızlı API örneği

```bash
# Doküman yükle
curl -X POST http://localhost:3000/api/documents/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@sozlesme.pdf"

# Soru sor (SSE ile stream yanıt)
curl -N -X POST http://localhost:3000/api/chat/ask \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Fesih şartları nedir?", "document_id": 1}'
```

Sohbet uç noktası Server-Sent Events döndürür:
`meta` → `approval_required` → `answer_chunk` → `end`.

Tam API referansı, hat detayları ve deployment topolojileri için [Mimari](ARCHITECTURE.tr.md) dokümanına bakın.

---

<p align="center">
  <a href="../README.tr.md"><strong>🏠 Ana Sayfa</strong></a>
  &nbsp;·&nbsp;
  <strong>✨ Özellikler</strong>
  &nbsp;·&nbsp;
  <a href="BENCHMARK.tr.md"><strong>📈 Benchmark</strong></a>
  &nbsp;·&nbsp;
  <a href="ARCHITECTURE.tr.md"><strong>🏗️ Mimari</strong></a>
  &nbsp;·&nbsp;
  <a href="DOCUMENT_INGESTION.tr.md"><strong>📊 Doküman İşleme</strong></a>
  &nbsp;·&nbsp;
  <a href="SCREENSHOTS.tr.md"><strong>📸 Ekran Görüntüleri</strong></a>
  &nbsp;·&nbsp;
  <a href="../CONTRIBUTING.tr.md"><strong>🤝 Katkı</strong></a>
  &nbsp;·&nbsp;
  <a href="../CHANGELOG.md"><strong>📝 Changelog</strong></a>
</p>
