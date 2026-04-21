# Septum — Özellik ve Tespit Referansı

<p align="center">
  <a href="../README.tr.md"><strong>🏠 Ana Sayfa</strong></a>
  &nbsp;·&nbsp;
  <strong>✨ Özellikler</strong>
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
- [Benchmark Sonuçları](#benchmark-sonuçları)
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
| 2 | **NER** — dile göre model seçen HuggingFace XLM-RoBERTa (20+ dil). BÜYÜK HARF girdi, çıkarım öncesinde otomatik olarak başlık harflerine çevrilir. LOCATION ve ORGANIZATION_NAME çıktıları, ortak isimlerden kaynaklanan yalancı pozitifleri ayıklayan "çok kelimeli veya yüksek skor" kapısından geçer (ayrıntılar için *Kapsam ve sınırlar* bölümüne bakın). | PERSON_NAME, LOCATION, ORGANIZATION_NAME |
| 3 | **Ollama** — bağlam doğrulama, takma ad tespiti ve semantik varlıklar için yerel LLM. | PERSON_NAME takma adları; DIAGNOSIS, MEDICATION, RELIGION, POLITICAL_OPINION, SEXUAL_ORIENTATION, ETHNICITY, CLINICAL_NOTE, BIOMETRIC_ID, DNA_PROFILE |

**Coreference çözümleme.** Üç katman span'lerini ürettikten sonra sanitizer, aynı kişiye yapılan tüm atıfları tek bir placeholder altında toplar. Aynı dokümandaki `"John"`, `"J. Doe"` ve `"Mr. Doe"` ifadelerinin tamamı tek bir `[PERSON_1]`'e indirgenir. Çözümleme yalnızca cümleler arasında değil, aynı dokümanın farklı parçaları arasında da çalışır.

**3. katman isteğe bağlıdır.** Ayarlardan `use_ollama_semantic_layer=false` yaparak devre dışı bırakabilirsiniz. 1. ve 2. katmanlar yapısal kimlikleri ve isimleri yakalar; 3. katman ise regex ve NER'in göremediği hassas kategorileri (sağlık, din, siyasi görüş vb.) tespit eder. Doğruluk, seçilen Ollama modeline bağlıdır — aşağıdaki benchmark `aya-expanse:8b` ile alınmıştır.

---

## Benchmark Sonuçları

Benchmark, 17 hazır regülasyonun tamamı aktifken **beş bağımsız veri kaynağı ve iki dayanıklılık probu** üzerinde koşturuldu:

1. **Septum sentetik korpus** — **16 dilde** (ar, de, en, es, fr, hi, it, ja, ko, nl, pl, pt, ru, th, tr, zh) 23 varlık tipi üzerinde algoritmik olarak üretilmiş **3.468 PII değeri**. Hiçbir kamuya açık veri kümesinin taşımadığı checksum'lı kimlikleri (geçerli Luhn, IBAN MOD-97, TCKN) kapsamanın tek yolu budur; ayrıca Ollama'nın kendine özgü katkısını ölçmek için 31 dokümanlık semantik-bağlamsal bir alt küme (DIAGNOSIS / MEDICATION / RELIGION / POLITICAL_OPINION / ETHNICITY / SEXUAL_ORIENTATION — 60 varlık) eklenmiştir. Seed sabittir — sonuçlar bire bir tekrarlanabilir.
2. **Microsoft [presidio-evaluator](https://github.com/microsoft/presidio-research)** — 200 sentetik Faker cümlesi; Presidio ekibinin kullandığı referans PII değerlendirme çerçevesi. Septum'un 17 regülasyonunda PII sayılmayan etiketler (DATE_TIME / TIME / TITLE / SEX / CARDISSUER / NRP) entity map'ten hariç tutulur; böylece recall, maskeleme kalitesini yansıtır, kategori uyumsuzluğunu değil.
3. **[Babelscape/wikineural](https://huggingface.co/datasets/Babelscape/wikineural)** — 9 dilde × 50'şer Wikipedia held-out cümlesi. Uyarı: Septum'un kullandığı XLM-RoBERTa NER modelleri ilgili WikiANN korpusu üzerinde eğitildiğinden, bu sayılar katı bir OOD testi değil, üst sınıra yakın değerlerdir.
4. **[ai4privacy/pii-masking-300k](https://huggingface.co/datasets/ai4privacy/pii-masking-300k)** — 6 dilde (en/de/fr/es/it/nl) × 50'şer validation cümlesi. Sıfırdan yazılmış, modern ve PII odaklı bir veri kümesi; Septum'un kullandığı modeller bu veri üzerinde eğitilmediği için benchmark'ın gerçek out-of-distribution (OOD) testine en yakınlaştığı kaynaktır.
5. **[CoNLL-2003](https://aclanthology.org/W03-0419/)** — klasik İngilizce haber alanının held-out test setinden 200 cümle. Septum'la ilgili hiçbir eğitim korpusunda yer almaz.
6. **[DFKI-SLT/few-nerd](https://huggingface.co/datasets/DFKI-SLT/few-nerd)** (supervised split) — çok alanlı Wikipedia NER; cross-domain dayanıklılık probu olarak kullanılır. Bu kümeden yalnızca person / organization / location etiketleri Septum PII kategorilerine karşılık gelir.
7. **Dayanıklılık probları** — 9 dilde 15 PII içermeyen paragraf (yanlış pozitif oranı ölçümü) ve 18 gerçekçi biçimde gizlenmiş PII girdisi (leetspeak, Unicode homoglyph, zero-width birleştirici, karışık harfli e-posta, parantez içinde e-posta, yorum içine gizlenmiş kredi kartı, satır sonunda bölünmüş TCKN/IBAN, uluslararası telefon formatları ve "at / dot" biçiminde ASCII gizleme).

<p align="center">
  <a href="#benchmark-sonuçları"><img src="../assets/benchmark-f1-by-type.svg" alt="Varlık tipine göre F1 skoru" width="1100" /></a>
</p>

<p align="center">
  <a href="#benchmark-sonuçları"><img src="../assets/benchmark-layer-comparison.svg" alt="Hat katmanına göre tespit doğruluğu" width="820" /></a>
</p>

### Septum sentetik korpus (katman bazında)

| Katman | Varlık | Tip | Precision | Recall | F1 |
|:---|:---:|:---:|:---:|:---:|:---:|
| **Presidio (L1)** — örüntü + doğrulayıcı (controlled + extended + adversarial) | 1.710 | 20 | %100 | %96,4 | %98,2 |
| **NER (L2)** — XLM-RoBERTa + BÜYÜK HARF normalize (16 dil) | 840 | 3 | %99,9 | %90,8 | %95,1 |
| **Ollama (L3)** — aya-expanse:8b (alias + semantik-bağlamsal) | 918 | 9 | %99,9 | %90,6 | %95,0 |
| **Birleşik** | **3.468** | **23** | **%99,9** | **%93,5** | **%96,6** |

**Ollama semantik alt kümesi** (DIAGNOSIS / MEDICATION / RELIGION / POLITICAL_OPINION / ETHNICITY / SEXUAL_ORIENTATION — Presidio ve NER'in ifade edemediği kategoriler): 31 dokümanda 60 varlık, **F1 %96,6** (Precision %98,3, Recall %95,0).

**Ollama ablation** — aynı 205 dokümanlı korpus (semantik + alias + NER) Ollama AÇIK ve KAPALI olacak şekilde iki kez çalıştırıldığında fark: **+3,49 pp recall, +1,95 pp F1**. Ollama'nın marjinal değerinin dürüst ölçümü budur. Semantik alt küme tek başına ele alındığında ise Ollama recall'u neredeyse sıfırdan %95'e çıkarır; çünkü bu kategorileri ifade edebilen başka bir katman yoktur.

### Dış referans veri kümeleri

| Kaynak | Varlık | Tip | Precision | Recall | F1 |
|:---|:---:|:---:|:---:|:---:|:---:|
| **Microsoft presidio-evaluator** (EN, sentetik Faker, 200 cümle) | 326 | 11 | %98,2 | %66,6 | %79,3 |
| **Babelscape/wikineural** (9 dil × 50 = 450 cümle, held-out Wikipedia NER) | 634 | 3 | %95,5 | %76,5 | %84,9 |
| **ai4privacy/pii-masking-300k** (6 dil, gerçek OOD — eğitim verisinde yok) | 1.456 | 12 | %96,4 | %54,5 | %69,6 |
| **CoNLL-2003** (EN haber, 372 PER/ORG/LOC puanlandı + 35 MISC bilinçli hariç) | 372 | 3 | %97,9 | %37,4 | %54,1 |
| **DFKI-SLT/few-nerd** (çok-alanlı Wikipedia, 200 test cümlesi) | 361 | 3 | %95,7 | %68,4 | %79,8 |

CoNLL-2003 recall'unun bir bölümü yapısaldır: 407 gold varlığın 35'i (%**8,6**) MISC sınıfıdır — milliyet, olay, eser gibi — ve Septum'un 17 regülasyonunda PII sayılmadığından skorlamanın dışında bırakılır. Tablodaki %54,1, MISC düşürüldükten *sonraki* recall'u yansıtır ve iki etkenin birleşimidir: (a) Septum'un serbest haber metninde tek başına geçen yer adlarını PII olarak ele almaması (GDPR Art. 4(1) gerekçesi) ve (b) kısa, tek token'lık mention'lar üzerindeki conservative LOCATION / ORGANIZATION kapıları. Ai4Privacy ise mevcut regülasyon paketlerinin doğrudan hedeflemediği USERNAME ve ince taneli adres alt tiplerindeki gerçek açıkları ortaya koyuyor — bu bir yuvarlama hatası değil, aksiyon alınabilir bir sinyaldir.

<p align="center">
  <a href="#benchmark-sonuçları"><img src="../assets/benchmark-external-validation.svg" alt="Dış doğrulama — Septum sentetik vs presidio-evaluator vs wikineural vs ai4privacy vs CoNLL-2003 vs Few-NERD vs adversarial pack" width="820" /></a>
</p>

### Dayanıklılık

| Prob | Hacim | Sonuç |
|:---|:---:|:---:|
| **Temiz metin yanlış pozitif oranı** (9 dilde 439 tokenli 15 PII-içermeyen paragraf) | 0 FP | **0,00 FP / 1k token** |
| **Adversarial paket** (18 gerçekçi biçimde gizlenmiş PII girdisi: leetspeak, Unicode homoglyph, zero-width birleştirici, karışık harfli e-posta, parantez içinde e-posta, yorum içinde gizlenmiş kredi kartı, satır sonunda bölünmüş TCKN/IBAN, uluslararası telefon formatları, "at / dot" biçiminde ASCII gizleme) | 20 yerleştirilmiş | P %100 · R %90,0 · **F1 %94,7** |

Adversarial paket, bilinçli olarak gerçek bir kullanıcının ya da saldırganın deneme olasılığı yüksek olan girdilere odaklanır. Kimsenin gerçek hayatta yapıştırmayacağı aşırı yapay kurgular (örneğin üç boşluklu IBAN) dışarıda bırakıldığı için sonuç, stres testi tiyatrosunu değil gerçek dünya dayanıklılığını ölçer. %10'luk recall açığı, obfuskasyonu normalleştiren bir özel kural katmanının nerede fark yaratacağını gösteriyor.

### Dil bazlı kırılım (Ollama hattı)

<p align="center">
  <a href="#benchmark-sonuçları"><img src="../assets/benchmark-per-language.svg" alt="Tam Ollama hattında dil bazlı tespit doğruluğu — 16 dil" width="900" /></a>
</p>

F1, Latin alfabesini kullanan dillerde (EN %98,3, DE %100, ES %100, FR %95,8, IT %100, NL %100, PL %98,6, PT %97,1, RU %100, TR %96,8) birbirine yakın ve oldukça yüksek seyrediyor; Arapça (%92,3) ve Hintçe'de (%100) de güçlü kalıyor. Gerçek zayıf noktalar CJK ve Tayca tarafındadır: **Tayca %87,1, Korece %83,3, Japonca %65,4, Çince %54,2**. CJK için minimum span uzunluğu eşiği artık dile duyarlı (ZH / JA / KO / TH için 2 glif, diğer diller için 3 karakter); bu değişiklik tek başına Çinceyi %44,4'ten %54,2'ye taşıdı. Geri kalan açığı kapatmak dil başına NER ince ayarı gerektiriyor; bu yol haritasındadır, bugün çözülmüş bir kazanım olarak sunulmamaktadır.

> NER (L2), otomatik başlık-harfi normalizasyonu sayesinde tıbbi ve hukuki dokümanlarda sık rastlanan BÜYÜK HARF isimleri de yakalar; kurum adlarını da tanır. LOCATION çıktısı conservative bir filtreden geçirilir (çok kelimeli **veya** güven skoru ≥ 0,95); böylece "Doğum" ya da Almanca form başlıkları gibi ortak isim kaynaklı yalancı pozitifler elenir, "İstanbul" ve "Berlin" gibi gerçek yer adları ise geçişini sürdürür. Ollama (L3) adayları doğrular ve takma adları toparlar. Veri kümesi; boşluklu IBAN, noktalı telefon gibi zorlayıcı formatları da kapsadığı için Presidio'nun recall değerini gerçek dünyadaki seviyeye yaklaştırır. Testi kendi ortamınızda da çalıştırabilirsiniz:
> `pytest packages/api/tests/benchmark_detection.py -v -s`

### Kapsam ve sınırlar

**Hiçbir PII tespit sistemi %100 doğru değildir.** Septum'un benchmark'ı nerede güçlü, nerede zayıf olduğu konusunda şeffaftır:

- **LOCATION çıktısı çok-kelimeli-veya-yüksek-skor kapısından geçer** (ORGANIZATION_NAME ile aynı mimari yaklaşım). Çok dilli XLM-RoBERTa modelleri, Septum'un desteklediği her dilde ortak isimler ve form alan başlıklarında stokastik tek token LOC yalancı pozitifleri üretir (Türkçe "Doğum", Almanca form başlıkları vb.). Bu yalancı pozitifleri dil başına stopword listesiyle kovalamak 50+ lokalde ölçeklenmez. Söz konusu kapı, 0,95 güven skorunun altındaki tek token span'leri eler; "İstanbul" ve "Berlin" gibi gerçek yer isimleri tipik olarak 0,97 ve üzeri skor aldığı için geçer, "New York" gibi çok kelimeli lokasyonlar ise skor kapısını tamamen atlar. Yapılandırılmış adres PII'si buna ek olarak Presidio'nun `StructuralAddressRecognizer`'ı ve regülasyon bazlı POSTAL_ADDRESS / STREET_ADDRESS tanıyıcılarıyla yakalanır.
- **37 regülasyon varlık tipinin tamamı tespit edilebilir durumdadır** — 21'i Presidio, 3'ü NER, 9'u Ollama tarafından; geri kalanı ise ana tip kapsamıyla (FIRST_NAME → PERSON_NAME, CITY → LOCATION vb.).
- **Aktif benchmark kapsamı: 16 dilde 3.468 değer üzerinden 23 varlık tipi.**
- **Semantik tipler** (DIAGNOSIS, MEDICATION, RELIGION, POLITICAL_OPINION) yalnızca Ollama katmanı tarafından yakalanır; bunun için yerel bir LLM'in çalışır durumda olması şarttır.
- **Bağlama bağlı tanıyıcılar** (DATE_OF_BIRTH, PASSPORT_NUMBER, SSN, TAX_ID) yalancı pozitif oranını düşürmek için değerin yakınında bağlam anahtar kelimesi arar; 8'den fazla dilde anahtar kelime listesi mevcuttur.
- **Zorlayıcı formatlar** (boşluklu TCKN, noktalı telefon) kontrollü format testlerine kıyasla daha düşük tespit oranı verir. Benchmark bu durumu dürüstçe raporlar.

**Onay Mekanizması güvenlik ağı işlevi görür.** LLM'e gönderilmeden önce tam olarak ne çıkacağını görürsünüz ve gerektiğinde reddedersiniz. Otomatik tespit riski düşürür; son kararı veren insan denetimi ise riski tamamen ortadan kaldırır.

Benchmark modelleri: NER, Türkçe için `akdeniz27/xlm-roberta-base-turkish-ner`, diğer diller için `Davlan/xlm-roberta-base-wikiann-ner` kullanır. Ollama katmanı `aya-expanse:8b` ile çalışır. Daha büyük Ollama modelleri genelde semantik tespiti iyileştirir; ancak bunun bedeli artan gecikmedir.

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
