# Septum — Benchmark Sonuçları

<p align="center">
  <a href="../readme.md"><strong>🏠 Ana Sayfa</strong></a>
  &nbsp;·&nbsp;
  <a href="installation.md"><strong>🚀 Kurulum</strong></a>
  &nbsp;·&nbsp;
  <strong>📈 Benchmark</strong>
  &nbsp;·&nbsp;
  <a href="features.md"><strong>✨ Özellikler</strong></a>
  &nbsp;·&nbsp;
  <a href="architecture.md"><strong>🏗️ Mimari</strong></a>
  &nbsp;·&nbsp;
  <a href="document-ingestion.md"><strong>📊 Doküman İşleme</strong></a>
  &nbsp;·&nbsp;
  <a href="screenshots.md"><strong>📸 Ekran Görüntüleri</strong></a>
</p>

---

Benchmark, 17 hazır regülasyonun tamamı aktifken **beş bağımsız veri kaynağı ve iki dayanıklılık probu** üzerinde yürütüldü:

1. **Septum sentetik korpusu** — 16 dilde (ar, de, en, es, fr, hi, it, ja, ko, nl, pl, pt, ru, th, tr, zh), 23 varlık tipi üzerinde algoritmik olarak üretilmiş **3.468 PII değeri**. Kamuya açık hiçbir veri kümesinin taşımadığı checksum'lı kimlikleri (geçerli Luhn, IBAN MOD-97, TCKN) kapsayabilmenin yegâne yolu budur. Korpusa ayrıca Ollama'nın kendine özgü katkısını ölçmek için 31 dokümanlık semantik-bağlamsal bir alt küme (DIAGNOSIS / MEDICATION / RELIGION / POLITICAL_OPINION / ETHNICITY / SEXUAL_ORIENTATION — toplam 60 varlık) eklenmiştir. Seed sabit olduğundan sonuçlar bire bir tekrarlanabilir.
2. **Microsoft [presidio-evaluator](https://github.com/microsoft/presidio-research)** — 200 sentetik Faker cümlesinden oluşan, Presidio ekibinin referans olarak benimsediği PII değerlendirme çerçevesi. Septum'un 17 regülasyonunun hiçbirinde PII sayılmayan etiketler (DATE_TIME / TIME / TITLE / SEX / CARDISSUER / NRP) varlık eşlemesinin dışında bırakılır; böylece recall, kategori uyumsuzluğunu değil maskeleme kalitesini yansıtır.
3. **[Babelscape/wikineural](https://huggingface.co/datasets/Babelscape/wikineural)** — 9 dilde 50'şer Wikipedia cümlesinden oluşan kenara ayrılmış (held-out) küme. Bir uyarı gerekir: Septum'un kullandığı XLM-RoBERTa NER modelleri, akraba [WikiANN korpusu](https://huggingface.co/datasets/unimelb-nlp/wikiann) üzerinde eğitildiğinden, buradaki sayılar katı bir OOD testi değil, üst sınıra yakın değerler olarak okunmalıdır.
4. **[ai4privacy/pii-masking-300k](https://huggingface.co/datasets/ai4privacy/pii-masking-300k)** — 6 dilde (en/de/fr/es/it/nl) 50'şer validation cümlesi. Sıfırdan ve PII odağıyla kurulmuş modern bir veri kümesi; Septum'un kullandığı modeller bu veri üzerinde eğitilmediğinden, benchmark'ın gerçek bir out-of-distribution testine en çok yaklaştığı kaynak burasıdır.
5. **[CoNLL-2003](https://aclanthology.org/W03-0419/)** — klasik İngilizce haber alanının kenara ayrılmış test setinden 200 cümle. Septum'la ilgili hiçbir eğitim korpusunda yer almaz.
6. **[DFKI-SLT/few-nerd](https://huggingface.co/datasets/DFKI-SLT/few-nerd)** (supervised split) — çok alanlı Wikipedia NER verisi; etki alanları arası dayanıklılık probu olarak kullanılır. Bu kümeden yalnızca person / organization / location etiketleri Septum'un PII kategorilerine karşılık düşer.
7. **Dayanıklılık probları** — 9 dilde, PII içermeyen 15 paragraf (yanlış pozitif oranının ölçümü için) ve 18 gerçekçi biçimde gizlenmiş PII girdisi: leetspeak, Unicode homoglyph, sıfır genişlikli birleştirici karakterler, karma harfli e-postalar, köşeli parantez içinde e-posta, yorum içine saklanmış kredi kartı, satır sonunda bölünmüş TCKN ve IBAN, uluslararası telefon formatları, "at / dot" biçiminde ASCII gizleme.

<p align="center">
  <a href="#septum--benchmark-sonuçları"><img src="../assets/benchmark-f1-by-type.svg" alt="Varlık tipine göre F1 skoru" width="1100" /></a>
</p>

<p align="center">
  <a href="#septum--benchmark-sonuçları"><img src="../assets/benchmark-layer-comparison.svg" alt="Hat katmanına göre tespit doğruluğu" width="820" /></a>
</p>

## Septum sentetik korpusu (katman bazında)

| Katman | Varlık | Tip | Precision | Recall | F1 |
|:---|:---:|:---:|:---:|:---:|:---:|
| **Presidio (L1)** — örüntü + doğrulayıcı (kontrollü + genişletilmiş + adversarial) | 1.710 | 20 | %100 | %96,4 | %98,2 |
| **NER (L2)** — XLM-RoBERTa + BÜYÜK HARF normalizasyonu (16 dil) | 840 | 3 | %99,9 | %90,8 | %95,1 |
| **Ollama (L3)** — aya-expanse:8b (takma ad + semantik-bağlamsal) | 918 | 9 | %99,9 | %90,6 | %95,0 |
| **Birleşik** | **3.468** | **23** | **%99,9** | **%93,5** | **%96,6** |

**Ollama semantik alt kümesi** — Presidio ve NER'in ifade edemediği kategoriler (DIAGNOSIS / MEDICATION / RELIGION / POLITICAL_OPINION / ETHNICITY / SEXUAL_ORIENTATION): 31 dokümanda 60 varlık, **F1 %96,6** (Precision %98,3, Recall %95,0).

**Ollama dışlama (ablation) testi** — aynı 205 dokümanlık korpus (semantik + takma ad + NER), Ollama bir kez AÇIK bir kez KAPALI çalıştırıldığında aradaki fark: **+3,49 puan recall, +1,95 puan F1**. Ollama'nın marjinal değerinin dürüst ölçüsü budur. Semantik alt küme tek başına ele alındığında ise Ollama, recall'u neredeyse sıfırdan %95'e taşır; zira bu kategorileri ifade edebilecek başka hiçbir katman yoktur.

## Dış referans veri kümeleri

| Kaynak | Varlık | Tip | Precision | Recall | F1 |
|:---|:---:|:---:|:---:|:---:|:---:|
| **Microsoft presidio-evaluator** (EN, sentetik Faker, 200 cümle) | 326 | 11 | %98,2 | %66,6 | %79,3 |
| **Babelscape/wikineural** (9 dil × 50 = 450 cümle, kenara ayrılmış Wikipedia NER) | 634 | 3 | %95,5 | %76,5 | %84,9 |
| **ai4privacy/pii-masking-300k** (6 dil, gerçek OOD — eğitim verisinde yer almaz) | 1.456 | 12 | %96,4 | %54,5 | %69,6 |
| **CoNLL-2003** (EN haber, 372 PER/ORG/LOC puanlandı + 35 MISC bilinçli olarak hariç) | 372 | 3 | %97,9 | %37,4 | %54,1 |
| **DFKI-SLT/few-nerd** (çok alanlı Wikipedia, 200 test cümlesi) | 361 | 3 | %95,7 | %68,4 | %79,8 |

CoNLL-2003'teki recall'un bir bölümü yapısal nedenlere bağlıdır: 407 gold varlığın 35'i (**%8,6**) MISC sınıfındadır — milliyet, olay adı, eser başlığı gibi. Bu etiketler Septum'un 17 regülasyonunun hiçbirinde PII sayılmadığından skorlamanın dışında tutulur. Tabloda görünen %54,1, MISC çıkarıldıktan *sonraki* recall değeridir ve iki etkenin bileşkesidir: (a) Septum'un, serbest haber metninde tek başına geçen yer adlarını tek başına PII saymama tercihi ([GDPR Art. 4(1)](https://gdpr-info.eu/art-4-gdpr/) gerekçesi); (b) kısa, tek kelimelik atıflarda devreye giren dar tanımlı LOCATION / ORGANIZATION filtreleri. Ai4Privacy ise farklı bir şey söylüyor: mevcut regülasyon paketlerinin doğrudan hedeflemediği USERNAME ve ince taneli adres alt tiplerinde gerçek açıkların bulunduğunu. Bu, bir yuvarlama hatası değil; harekete geçilmesi gereken bir sinyaldir.

<p align="center">
  <a href="#septum--benchmark-sonuçları"><img src="../assets/benchmark-external-validation.svg" alt="Dış doğrulama — Septum sentetik vs presidio-evaluator vs wikineural vs ai4privacy vs CoNLL-2003 vs Few-NERD vs adversarial paket" width="820" /></a>
</p>

## Dayanıklılık

| Prob | Hacim | Sonuç |
|:---|:---:|:---:|
| **Temiz metin yanlış pozitif oranı** (9 dilde 439 token, PII içermeyen 15 paragraf) | 0 FP | **0,00 FP / 1k token** |
| **Adversarial paket** (18 gerçekçi gizlenmiş PII girdisi: leetspeak, Unicode homoglyph, sıfır genişlikli birleştirici, karma harfli e-posta, köşeli parantezli e-posta, yoruma gömülü kredi kartı, satır sonunda bölünmüş TCKN/IBAN, uluslararası telefon formatları, "at / dot" ASCII gizlemesi) | 20 yerleştirilmiş | P %100 · R %90,0 · **F1 %94,7** |

Adversarial paket, kasıtlı olarak gerçek bir kullanıcının ya da saldırganın deneme olasılığı yüksek olan girdilere odaklanır. Kimsenin hayatta yapıştırmayacağı aşırı yapay kurgular — mesela üç boşlukla ayrılmış bir IBAN — dışarıda bırakıldığı için sonuç, stres testi gösterisini değil gerçek dünyadaki dayanıklılığı yansıtır. %10'luk recall açığı, gizleme girişimlerini normalleştirecek bir özel kural katmanının nerede fark yaratabileceğini açıkça gösteriyor.

## Dil bazlı kırılım (Ollama hattı)

<p align="center">
  <a href="#septum--benchmark-sonuçları"><img src="../assets/benchmark-per-language.svg" alt="Tam Ollama hattında dil bazlı tespit doğruluğu — 16 dil" width="900" /></a>
</p>

F1, Latin alfabesi kullanan dillerde (EN %98,3, DE %100, ES %100, FR %95,8, IT %100, NL %100, PL %98,6, PT %97,1, RU %100, TR %96,8) birbirine yakın ve oldukça yüksek seyrediyor; Arapça (%92,3) ve Hintçe'de (%100) de güçlü kalıyor. Gerçek zayıf noktalar CJK ve Tayca kanadındadır: **Tayca %87,1, Korece %83,3, Japonca %65,4, Çince %54,2**. CJK için asgari aralık uzunluğu eşiği artık dile duyarlı (ZH / JA / KO / TH için 2 glif, diğer dillerde 3 karakter); bu tek değişiklik Çince'yi %44,4'ten %54,2'ye taşıdı. Kalan açığı kapatmak dil başına NER ince ayarı gerektiriyor; bu, yol haritasında duran bir hedeftir — bugün çözülmüş bir kazanım olarak sunulmuyor.

> NER (L2), otomatik başlık-harfi normalizasyonu sayesinde tıbbi ve hukuki dokümanlarda sık görülen BÜYÜK HARFLİ isimleri de yakalar; kurum adlarını da tanır. LOCATION çıktısı dar tanımlı bir filtreden geçirilir (çok kelimeli **veya** güven skoru ≥ 0,95); böylece "Doğum" ya da Almanca form başlıkları gibi ortak isim kaynaklı yalancı pozitifler elenirken "İstanbul" ve "Berlin" gibi gerçek yer adları geçişini korur. Ollama (L3), adayları doğrular ve takma adları toparlar. Veri kümesi; boşluklu IBAN, noktalı telefon gibi zorlayıcı formatları da kapsadığından Presidio'nun recall değerini gerçek dünyadaki seviyeye yaklaştırır. Testi kendi ortamınızda da çalıştırabilirsiniz:
> [`pytest packages/api/tests/benchmark_detection.py -v -s`](../packages/api/tests/benchmark_detection.py)

## Kapsam ve sınırlar

**Hiçbir PII tespit sistemi %100 doğru değildir.** Septum'un benchmark'ı, nerede güçlü, nerede zayıf olduğu konusunda şeffaf kalmayı tercih eder:

- **LOCATION çıktısı, çok-kelimeli-veya-yüksek-skor filtresinden geçer** (ORGANIZATION_NAME ile aynı mimari yaklaşım). Çok dilli XLM-RoBERTa modelleri, Septum'un desteklediği her dilde, ortak isimlerde ve form alan başlıklarında rastgele tek token'lık LOC yalancı pozitifleri üretir (Türkçe "Doğum", Almanca form başlıkları vb.). Bu yalancı pozitifleri dil başına stopword listesiyle tek tek takip etmek 50'den fazla lokalde ölçeklenmez. Filtre, 0,95 güven skorunun altındaki tek token'lık aralıkları eler; "İstanbul" ve "Berlin" gibi gerçek yer adları tipik olarak 0,97 ve üzeri skor aldığı için geçer, "New York" gibi çok kelimeli yer adları ise skor kapısına hiç takılmaz. Yapılandırılmış adres PII'si buna ek olarak Presidio'nun `StructuralAddressRecognizer`'ı ve regülasyon bazlı POSTAL_ADDRESS / STREET_ADDRESS tanıyıcıları tarafından yakalanır.
- **37 regülasyon varlık tipinin tamamı tespit edilebilir.** 21'i Presidio, 3'ü NER, 9'u Ollama tarafından; geri kalanı ise üst tip kapsamıyla (FIRST_NAME → PERSON_NAME, CITY → LOCATION vb.).
- **Aktif benchmark kapsamı:** 16 dilde 3.468 değer üzerinden 23 varlık tipi.
- **Semantik tipler** (DIAGNOSIS, MEDICATION, RELIGION, POLITICAL_OPINION) yalnızca Ollama katmanı tarafından yakalanır; bu tiplerin devrede olması için yerel bir LLM'in çalışır durumda bulunması şarttır.
- **Bağlama bağlı tanıyıcılar** (DATE_OF_BIRTH, PASSPORT_NUMBER, SSN, TAX_ID), yalancı pozitif oranını düşürebilmek için değerin yakınında bağlam anahtar kelimesi arar; anahtar kelime listeleri 8'den fazla dili kapsar.
- **Zorlayıcı formatlar** (boşluklu TCKN, noktalı telefon), kontrollü format testlerine kıyasla daha düşük tespit oranı verir. Benchmark bu durumu olduğu gibi raporlar.

**Onay mekanizması güvenlik ağı işlevi görür.** LLM'e gönderilmeden önce tam olarak ne gideceğini görür, gerektiğinde reddedersiniz. Otomatik tespit riski düşürür; son kararı veren insan denetimi ise riski tümüyle ortadan kaldırır.

Benchmark modelleri: NER, Türkçe için [`akdeniz27/xlm-roberta-base-turkish-ner`](https://huggingface.co/akdeniz27/xlm-roberta-base-turkish-ner), diğer diller için [`Davlan/xlm-roberta-base-wikiann-ner`](https://huggingface.co/Davlan/xlm-roberta-base-wikiann-ner) kullanır. Ollama katmanı [`aya-expanse:8b`](https://ollama.com/library/aya-expanse) üzerinde çalışır. Daha büyük Ollama modelleri semantik tespiti genelde iyileştirir; bedeli artan gecikmedir.

**Ek kaynaklar:**

- Benchmark test dosyası: [`packages/api/tests/benchmark_detection.py`](../packages/api/tests/benchmark_detection.py)
- Regülasyon bazlı tanıyıcı paketleri: [`packages/core/septum_core/recognizers/`](../packages/core/septum_core/recognizers/)
- Ulusal kimlik doğrulayıcıları (Luhn, Verhoeff, ISO 7064, TCKN mod-10/mod-11, CPF, NRIC): [`packages/core/septum_core/national_ids/`](../packages/core/septum_core/national_ids/)
- Her varlık tipinin hukuki dayanağı: [`regulation-entity-sources.md`](../packages/core/docs/regulation-entity-sources.md)
- Microsoft Presidio: [microsoft/presidio](https://github.com/microsoft/presidio) · değerlendirme çerçevesi: [microsoft/presidio-research](https://github.com/microsoft/presidio-research)
- XLM-RoBERTa makalesi: [Conneau vd., *Unsupervised Cross-lingual Representation Learning at Scale* (ACL 2020)](https://aclanthology.org/2020.acl-main.747/)
- CoNLL-2003 shared task makalesi: [Tjong Kim Sang & De Meulder, 2003](https://aclanthology.org/W03-0419/)
- Few-NERD makalesi: [Ding vd., *Few-NERD: A Few-shot Named Entity Recognition Dataset* (ACL 2021)](https://aclanthology.org/2021.acl-long.248/)
- Ai4Privacy veri kümesi kartı: [ai4privacy/pii-masking-300k](https://huggingface.co/datasets/ai4privacy/pii-masking-300k)
- Regülasyonların birincil kaynakları: [GDPR](https://gdpr-info.eu/) · [KVKK (6698 sayılı Kanun)](https://www.mevzuat.gov.tr/MevzuatMetin/1.5.6698.pdf) · [HIPAA](https://www.hhs.gov/hipaa/for-professionals/privacy/laws-regulations/index.html) · [CCPA / CPRA](https://oag.ca.gov/privacy/ccpa) · [LGPD](https://www.gov.br/anpd/pt-br) · [UK GDPR](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/) · [PIPEDA](https://www.priv.gc.ca/en/privacy-topics/privacy-laws-in-canada/the-personal-information-protection-and-electronic-documents-act-pipeda/)

---

<p align="center">
  <a href="../readme.md"><strong>🏠 Ana Sayfa</strong></a>
  &nbsp;·&nbsp;
  <a href="installation.md"><strong>🚀 Kurulum</strong></a>
  &nbsp;·&nbsp;
  <strong>📈 Benchmark</strong>
  &nbsp;·&nbsp;
  <a href="features.md"><strong>✨ Özellikler</strong></a>
  &nbsp;·&nbsp;
  <a href="architecture.md"><strong>🏗️ Mimari</strong></a>
  &nbsp;·&nbsp;
  <a href="document-ingestion.md"><strong>📊 Doküman İşleme</strong></a>
  &nbsp;·&nbsp;
  <a href="screenshots.md"><strong>📸 Ekran Görüntüleri</strong></a>
</p>
