# Septum — Benchmark Sonuçları

<p align="center">
  <a href="./index.md"><strong>🏠 Ana Sayfa</strong></a>
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
3. **[Babelscape/wikineural](https://huggingface.co/datasets/Babelscape/wikineural)** — 9 dilde 50'şer Wikipedia cümlesinden oluşan kenara ayrılmış (held-out) *veri kümesi*. Burada yalnızca değerlendirme verisi olarak kullanılır; kaldırılan Babelscape wikineural *modeli* ile aynı şey değildir. Bir uyarı gerekir: önceki NER setindeki XLM-RoBERTa NER modelleri, akraba [WikiANN korpusu](https://huggingface.co/datasets/unimelb-nlp/wikiann) üzerinde eğitildiğinden, buradaki sayılar katı bir OOD testi değil, üst sınıra yakın değerler olarak okunmalıdır.
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

> **Önceki NER model seti ile ölçülmüştür.** NER varsayılanları sonradan değişti. Önceki set, de, it, nl, pl ve ru için ve listelenmemiş tüm diller için yedek olarak Babelscape wikineural modelini kullanıyordu; XLM-RoBERTa WikiANN en, ar, zh, es, fr, pt, hi, bn, ur, vi ve ja için kullanılıyordu; Türkçe'nin kendi modelleri vardı. Davlan mBERT-hrl artık zh, ja, ko, de, it, nl, pl, ru ve yedek dil için kullanılıyor; wikineural modeli artık kullanılmıyor. NER katmanına bağlı sayılar — NER ve Birleşik satırları, Ollama ablation testi, dış veri kümeleri, dil bazlı kırılım, erişim çalıştırmaları ve sızıntı ölçümü — önceki set ile ölçüldü. Bu diller yeniden ölçülmedi; mevcut varsayılanlar için yeni bir sayı yoktur. Aşağıdaki Babelscape/wikineural satırı değerlendirme verisidir, kaldırılan model değil. Güncel dil eşlemesi için bkz. [Doküman İşleme](document-ingestion.md#ner-model-kapsamı).

## Septum sentetik korpusu (katman bazında)

| Katman | Varlık | Tip | Precision | Recall | F1 |
|:---|:---:|:---:|:---:|:---:|:---:|
| **Presidio (L1)** — örüntü + doğrulayıcı (kontrollü + genişletilmiş + adversarial) | 1.710 | 20 | %100 | %96,4 | %98,2 |
| **NER (L2, önceki NER seti)** — XLM-RoBERTa + BÜYÜK HARF normalizasyonu (16 dil) | 840 | 3 | %99,9 | %90,8 | %95,1 |
| **Ollama (L3)** — aya-expanse:8b (takma ad + semantik-bağlamsal) | 918 | 9 | %99,9 | %90,6 | %95,0 |
| **Birleşik** | **3.468** | **23** | **%99,9** | **%93,5** | **%96,6** |

**Ollama semantik alt kümesi** — Presidio ve NER'in ifade edemediği kategoriler (DIAGNOSIS / MEDICATION / RELIGION / POLITICAL_OPINION / ETHNICITY / SEXUAL_ORIENTATION): 31 dokümanda 60 varlık, **F1 %96,6** (Precision %98,3, Recall %95,0).

**Ollama dışlama (ablation) testi** — aynı 205 dokümanlık korpus (semantik + takma ad + NER), Ollama bir kez AÇIK bir kez KAPALI çalıştırıldığında aradaki fark: **+3,49 puan recall, +1,95 puan F1**. Ollama'nın marjinal değerinin dürüst ölçüsü budur. Semantik alt küme tek başına ele alındığında ise Ollama, recall'u neredeyse sıfırdan %95'e taşır; zira bu kategorileri ifade edebilecek başka hiçbir katman yoktur.

## Dış referans veri kümeleri

| Kaynak | Varlık | Tip | Precision | Recall | F1 |
|:---|:---:|:---:|:---:|:---:|:---:|
| **Microsoft presidio-evaluator** (EN, sentetik Faker, 200 cümle; önceki NER seti) | 326 | 11 | %98,2 | %66,6 | %79,3 |
| **Babelscape/wikineural** veri kümesi (9 dil × 50 = 450 cümle, kenara ayrılmış Wikipedia NER; önceki NER seti) | 634 | 3 | %95,5 | %76,5 | %84,9 |
| **ai4privacy/pii-masking-300k** (6 dil, gerçek OOD — eğitim verisinde yer almaz; önceki NER seti) | 1.456 | 12 | %96,4 | %54,5 | %69,6 |
| **CoNLL-2003** (EN haber, 372 PER/ORG/LOC puanlandı + 35 MISC bilinçli olarak hariç; önceki NER seti) | 372 | 3 | %97,9 | %37,4 | %54,1 |
| **DFKI-SLT/few-nerd** (çok alanlı Wikipedia, 200 test cümlesi; önceki NER seti) | 361 | 3 | %95,7 | %68,4 | %79,8 |

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

Önceki NER model seti ile ölçülen F1, Latin alfabesi kullanan dillerde (EN %98,3, DE %100, ES %100, FR %95,8, IT %100, NL %100, PL %98,6, PT %97,1, RU %100, TR %96,8) birbirine yakın ve oldukça yüksek seyrediyor; Arapça (%92,3) ve Hintçe'de (%100) de güçlü kalıyor. Gerçek zayıf noktalar CJK ve Tayca kanadındadır: **Tayca %87,1, Korece %83,3, Japonca %65,4, Çince %54,2**. CJK için asgari aralık uzunluğu eşiği artık dile duyarlı (ZH / JA / KO / TH için 2 glif, diğer dillerde 3 karakter); bu tek değişiklik Çince'yi %44,4'ten %54,2'ye taşıdı. Kalan açığı kapatmak dil başına NER ince ayarı gerektiriyor; bu, yol haritasında duran bir hedeftir — bugün çözülmüş bir kazanım olarak sunulmuyor.

> NER (L2), otomatik başlık-harfi normalizasyonu sayesinde tıbbi ve hukuki dokümanlarda sık görülen BÜYÜK HARFLİ isimleri de yakalar; kurum adlarını da tanır. LOCATION çıktısı dar tanımlı bir filtreden geçirilir (çok kelimeli **veya** güven skoru ≥ 0,95); böylece "Doğum" ya da Almanca form başlıkları gibi ortak isim kaynaklı yalancı pozitifler elenirken "İstanbul" ve "Berlin" gibi gerçek yer adları geçişini korur. Ollama (L3), adayları doğrular ve takma adları toparlar. Veri kümesi; boşluklu IBAN, noktalı telefon gibi zorlayıcı formatları da kapsadığından Presidio'nun recall değerini gerçek dünyadaki seviyeye yaklaştırır. Testi kendi ortamınızda da çalıştırabilirsiniz:
> [`pytest packages/api/tests/benchmark_detection.py -v -s`](../packages/api/tests/benchmark_detection.py)

## Kapsam ve sınırlar

**Hiçbir PII tespit sistemi %100 doğru değildir.** Septum'un benchmark'ı, nerede güçlü, nerede zayıf olduğu konusunda şeffaf kalmayı tercih eder:

- **LOCATION çıktısı, çok-kelimeli-veya-yüksek-skor filtresinden geçer** (ORGANIZATION_NAME ile aynı mimari yaklaşım). Çok dilli NER modelleri, Septum'un desteklediği her dilde, ortak isimlerde ve form alan başlıklarında rastgele tek token'lık LOC yalancı pozitifleri üretir (Türkçe "Doğum", Almanca form başlıkları vb.). Bu yalancı pozitifleri dil başına stopword listesiyle tek tek takip etmek 50'den fazla lokalde ölçeklenmez. Filtre, 0,95 güven skorunun altındaki tek token'lık aralıkları eler; "İstanbul" ve "Berlin" gibi gerçek yer adları tipik olarak 0,97 ve üzeri skor aldığı için geçer, "New York" gibi çok kelimeli yer adları ise skor kapısına hiç takılmaz. Yapılandırılmış adres PII'si buna ek olarak Presidio'nun `StructuralAddressRecognizer`'ı ve regülasyon bazlı POSTAL_ADDRESS / STREET_ADDRESS tanıyıcıları tarafından yakalanır.
- **37 regülasyon varlık tipinin tamamı tespit edilebilir.** 21'i Presidio, 3'ü NER, 9'u Ollama tarafından; geri kalanı ise üst tip kapsamıyla (FIRST_NAME → PERSON_NAME, CITY → LOCATION vb.).
- **Aktif benchmark kapsamı:** 16 dilde 3.468 değer üzerinden 23 varlık tipi.
- **Semantik tipler** (DIAGNOSIS, MEDICATION, RELIGION, POLITICAL_OPINION) yalnızca Ollama katmanı tarafından yakalanır; bu tiplerin devrede olması için yerel bir LLM'in çalışır durumda bulunması şarttır.
- **Bağlama bağlı tanıyıcılar** (DATE_OF_BIRTH, PASSPORT_NUMBER, SSN, TAX_ID), yalancı pozitif oranını düşürebilmek için değerin yakınında bağlam anahtar kelimesi arar; anahtar kelime listeleri 8'den fazla dili kapsar.
- **Zorlayıcı formatlar** (boşluklu TCKN, noktalı telefon), kontrollü format testlerine kıyasla daha düşük tespit oranı verir. Benchmark bu durumu olduğu gibi raporlar.
- **NER kapsamı dengesizdir.** Tayca NER fiilen yoktur; Çince, Japonca, Korece ve Rusça'da NER recall'u sınırlıdır. Bkz. [Doküman İşleme](document-ingestion.md#ner-model-kapsamı).

**Onay mekanizması güvenlik ağı işlevi görür.** LLM'e gönderilmeden önce tam olarak ne gideceğini görür, gerektiğinde reddedersiniz. Otomatik tespit riski düşürür; son kararı veren insan denetimi ise riski tümüyle ortadan kaldırır.

Benchmark modelleri (önceki NER seti): NER, Türkçe için [`akdeniz27/xlm-roberta-base-turkish-ner`](https://huggingface.co/akdeniz27/xlm-roberta-base-turkish-ner), diğer diller için [`Davlan/xlm-roberta-base-wikiann-ner`](https://huggingface.co/Davlan/xlm-roberta-base-wikiann-ner) modelini kullanıyordu; de, it, nl, pl ve ru için ve listelenmemiş diller için yedek olarak ise Babelscape wikineural modeli kullanılıyordu. Güncel dil bazlı modeller [Doküman İşleme](document-ingestion.md#ner-model-kapsamı) sayfasındadır. Ollama katmanı [`aya-expanse:8b`](https://ollama.com/library/aya-expanse) üzerinde çalışır. Daha büyük Ollama modelleri semantik tespiti genelde iyileştirir; bedeli artan gecikmedir.

## Ollama model karşılaştırması (K3 katmanı)

K3 semantik katmanı modülerdir; harness `SEPTUM_BENCHMARK_OLLAMA_MODEL=<model>`
ortam değişkenini kabul eder ve aday modeli buradan değiştirir. Çoklu
model sürücüsünü çalıştırın; aşağıdaki tablo donanımınızdaki canlı
sonuçlarla dolacak:

```bash
./scripts/benchmark_ollama_models.sh                          # varsayılan üçlü
./scripts/benchmark_ollama_models.sh llama3.2:3b qwen2.5:14b  # özel set
```

| Model | Parametre | VRAM ≈ | Precision | Recall | F1 | Notlar |
|:---|:---:|:---:|:---:|:---:|:---:|:---|
| [`llama3.2:3b`](https://ollama.com/library/llama3.2) | 3 B | 3 GB | _TBD_ | _TBD_ | _TBD_ | En hızlı; semantik recall'ın düşük olması beklenir |
| [`aya-expanse:8b`](https://ollama.com/library/aya-expanse) | 8 B | 5 GB | %99,9 | %90,6 | %95,0 | Mevcut varsayılan; çok dilli ince ayarlı |
| [`qwen2.5:14b`](https://ollama.com/library/qwen2.5) | 14 B | 9 GB | _TBD_ | _TBD_ | _TBD_ | En yüksek doğruluk beklenir; 8 B'ye göre ~3× gecikme |

Yukarıdaki katman tablosundaki rakamlar varsayılan `aya-expanse:8b`
çalıştırmasını yansıtır; diğer modellerin sonuçları host'a (CPU/GPU,
Ollama sürümü, sistem prompt'u) bağlıdır ve runner script'iyle
ölçülene kadar **TBD** bırakılmıştır. CPU'da gecikme parametre
sayısıyla doğrusal artar; tek 24 GB GPU'da 14 B modeller hâlâ sığar
fakat ~3× artan çağrı maliyeti ingest pipeline'ında baskın olur.

## Erişim (retrieval) benchmark'ı

Bu benchmark PII tespitini değil erişimi (retrieval) ölçer. Sanitizer'ın NER katmanı açık çalıştığı için bu bölümdeki sayılar ve sızıntı ölçümü önceki NER model setiyle kaydedilmiştir (üstteki nota bakın). Her soru için arama yığını, cevabı içeren chunk'ı döndürüyor mu sorusunu sorar; bunu bir kez ham soruyla, bir kez de maskelenmiş soruyla yapar. Parçalama, embedding modeli ve reranker değişikliklerinin karşılaştırılacağı temel ölçüm budur.

Çalıştırma belge parçalayıcıyı, chunk başına sanitizer'ı, FAISS vektör deposunu ve BM25 indeksini kullanır. Dense, BM25 ve hybrid erişim, üretimdeki VectorStore ve BM25 kodunu çağırır; buna `VectorStore.hybrid_search` de dahildir. auto_rag, chat'in belgeler arası aramasını yansıtır: aynı ilgililik eşiği ve belge başına aday sayısı kullanılır; bir parite testi eşiği, belge başına aday sayısını ve aday üst sınırını chat'in uygulamasına karşı sabitler. Benchmark belge bazlı aramaları sıralı (serial) çalıştırır, üretimde ise eşzamanlı (concurrent) çalışırlar; bu nedenle auto_rag gecikmesi bir üst sınırdır. LLM çağrılmaz; çalışma sabitlenmiş (pinned) bir embedder ile çevrimdışı yürür.

### Değerlendirme seti

| Öğe | Değer |
|:---|:---|
| Belgeler | 30 sentetik belge: 10 İngilizce, 10 Türkçe, 10 Çince |
| Sorular | 140: 129 cevaplanabilir, 11 olumsuz |
| Cevaplanabilir, tipe göre | lexical 45 · paraphrase 45 · entity 24 · cross-lingual 15 |
| Olumsuz | hiçbir belgenin cevaplamadığı 11 soru |
| İndeks | 187 chunk; her chunk kaynak belgesinde bir aralığa eşlenmiş |
| Digest | `df81576cd611d809dd59eed997ec789cced8315301796ea75bcc11b97179efec` (her fixture dosyası üzerinde SHA-256) |

Belgeler ve sorular elle yazılmıştır. Bir belgedeki her kişisel veri yuvası, sabit tohumlu (seed 42) bir üreticinin değeriyle doldurulur. Bir sorunun işaret ettiği değer, kendi belgesindeki değerdir; bu yüzden her çalıştırma aynı metni üretir.

### Kurulum

| Öğe | Değer |
|:---|:---|
| Embedding modeli | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42` (sabitlenmiş) |
| Boyut | 384 |
| Parçalayıcı | `pipeline`. Düz metin olan 18 belge 200 karakter örtüşmeli, 800 karakterlik kayan pencere kullanır. PDF etiketli 12 belge en fazla 1.200 karakterlik structured parçalayıcı kullanır; metinleri doğrudan verilir, PDF ayrıştırması yapılmaz |
| Erişim | ilk 10; auto-RAG ilgililik eşiği 0,35 |
| Sorgu modları | ham ve maskelenmiş; ana sonuç maskelenmiş sorgudur |
| Güven aralıkları | Ortalama için %95 yüzdelik bootstrap, 1.000 yeniden örnekleme, seed 42 |
| Gecikme | Sorgu başına 5 ölçülmüş tekrar, maskelenmiş sorgular, sorgu embedding'i dahil tek bir retriever çağrısı |
| Donanım | Apple M4 Max, 14 CPU çekirdeği, 10 torch thread, Darwin 27.0.0 arm64 |
| Seed | 42 |
| Commit | Temel commit `2b9db6eb` ile bu daldaki benchmark harness commit'leri (taban kaydedildiğinde harness henüz commit'lenmemişti) |

### Sonuçlar

**Ana sonuç: maskelenmiş sorgu, hybrid arama.** Her hücrede ortalama [%95 CI] yer alır.

| Dil | n | Recall@5 | Recall@10 | MRR | nDCG@10 |
|:---|:---:|:---:|:---:|:---:|:---:|
| en | 43 | 0,953 [0,884, 1,000] | 1,000 [1,000, 1,000] | 0,727 [0,631, 0,827] | 0,789 [0,712, 0,865] |
| tr | 43 | 0,977 [0,930, 1,000] | 1,000 [1,000, 1,000] | 0,822 [0,728, 0,901] | 0,861 [0,788, 0,923] |
| zh | 43 | 0,953 [0,884, 1,000] | 1,000 [1,000, 1,000] | 0,767 [0,663, 0,853] | 0,824 [0,746, 0,889] |
| **genel** | **129** | **0,961 [0,922, 0,992]** | **1,000 [1,000, 1,000]** | **0,772 [0,715, 0,824]** | **0,825 [0,779, 0,865]** |

**Karşılaştırılan retriever'lar (genel, n = 129), ham ve maskelenmiş sorgu modları.**

| Retriever | Mod | Recall@5 | Recall@10 | MRR | nDCG@10 |
|:---|:---|:---:|:---:|:---:|:---:|
| dense | ham | 0,953 [0,907, 0,984] | 1,000 [1,000, 1,000] | 0,748 [0,690, 0,801] | 0,809 [0,765, 0,849] |
| dense | maskelenmiş | 0,953 [0,907, 0,984] | 1,000 [1,000, 1,000] | 0,739 [0,681, 0,791] | 0,802 [0,758, 0,841] |
| bm25 | ham | 0,930 [0,876, 0,969] | 1,000 [1,000, 1,000] | 0,740 [0,677, 0,796] | 0,799 [0,753, 0,842] |
| bm25 | maskelenmiş | 0,930 [0,876, 0,969] | 1,000 [1,000, 1,000] | 0,741 [0,681, 0,797] | 0,800 [0,753, 0,842] |
| hybrid | ham | 0,961 [0,922, 0,992] | 1,000 [1,000, 1,000] | 0,783 [0,724, 0,834] | 0,833 [0,787, 0,872] |
| hybrid | maskelenmiş | 0,961 [0,922, 0,992] | 1,000 [1,000, 1,000] | 0,772 [0,715, 0,824] | 0,825 [0,779, 0,865] |
| auto_rag | ham | 0,550 [0,465, 0,636] | 0,612 [0,527, 0,690] | 0,441 [0,368, 0,514] | 0,483 [0,409, 0,555] |
| auto_rag | maskelenmiş | 0,527 [0,442, 0,612] | 0,589 [0,504, 0,674] | 0,406 [0,336, 0,476] | 0,450 [0,381, 0,523] |

**Olumsuzlarda auto-RAG (11 soru).** Bir soru, en az bir chunk 0,35 eşiğinin üstünde ya da eşitte skor aldığında yanlış erişim sayılır. Yanlış erişim oranı iki sorgu modunda da 0,727 [0,455, 0,909]'dur. En yüksek (top-1) skorlar örtüşür: ham sorgularda olumsuzların 90. yüzdeliği 0,577, cevaplanabilir soruların 10. yüzdeliği 0,473'tür.

**Gecikme** (maskelenmiş sorgular, milisaniye, tek makine):

| Retriever | p50 (ms) | p95 (ms) | Örnek |
|:---|:---:|:---:|:---:|
| dense | 11,749 | 12,932 | 645 |
| bm25 | 0,216 | 0,275 | 645 |
| hybrid | 11,972 | 13,050 | 645 |
| auto_rag | 351,882 | 371,848 | 700 |

Örnek sayısı, sorgu sayısının 5 tekrarla çarpımıdır; auto_rag tüm 140 soruyu, diğerleri 129 cevaplanabilir soruyu çalıştırır.

**Ingestion** (tek çalıştırma):

| Adım | Süre |
|:---|:---:|
| Parçalama, 30 belge | 1,356 s |
| Embedding ve indeksleme | 1,187 s |
| Chunk başına sanitizasyon, 30 belge | 10,132 s |
| Sızıntı bloğu sanitizasyonu, en / tr / zh | 14,566 s / 20,605 s / 29,095 s |
| Tüm çalıştırma, duvar saati | 505,603 s |

### PII sızıntı kontrolü

Sızıntı kontrolü, ham kişisel veri metninin sanitizasyondan sağ çıkıp çıkmadığını sorar. Retrieval belgeleri yerine ayrı bir dolgu metni üzerinde çalışır.

| Terim | Anlamı |
|:---|:---|
| Literal sızıntı | Değer, büyük/küçük harf duyarsız olarak, üst üste binen bir chunk'ın maskelenmiş metninde birebir geçer. Yer tutucular asla sayılmaz. |
| Fragment sızıntısı | Değerin kurtarılabilir bir parçası, maskelenmiş metinde değerin kendi konumunda kalır. Tanımlayıcılar (e-posta, telefon, IBAN): en az max(6, sıkıştırılmış uzunluğun yarısı, yukarı yuvarlanmış) harf ve rakamdan oluşan bir dizi. İsimler: en az 3 karakterlik bir kelime; Han, kana ve Hangul için en az max(2, uzunluğun yarısı, yukarı yuvarlanmış) karakterlik bir dizi. |
| Çapraz değer | İçinde bir chunk sınırı bulunan değer (bir chunk başlangıcı ya da bitişi değerin içine düşer). |
| Kontrol değeri | İçinde hiçbir chunk sınırı bulunmayan değer. |

Her dilin yuvasız korpus paragrafları dolgu olarak kullanılır. Her 120 ile 180 dolgu karakterinden sonra bir sentetik değer eklenir. Değer sayısı 300'den başlar ve en az 50 değer çapraz olana kadar ikiye katlanır (üst sınır 4.800); nihai sayılar en için 1.200, tr için 1.200, zh için 2.400'dür. Her dil, tek bir anonimleştirme haritasıyla chunk chunk sanitize edilir. Blok yalnızca kaydedilir; geçme ya da kalma eşiği yoktur.

**Dil bazında** (tüm değerler; literal ve fragment sızıntı oranları):

| Dil | Çapraz n | Literal: çapraz | Literal: kontrol | Literal: fark | Fragment: çapraz | Fragment: kontrol | Fragment: fark |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| en | 52 | 0,269 | 0,369 | -0,100 | 0,365 | 0,368 | -0,003 |
| tr | 58 | 0,362 | 0,271 | +0,091 | 0,534 | 0,275 | +0,260 |
| zh | 91 | 0,154 | 0,492 | -0,338 | 0,264 | 0,492 | -0,228 |

**ORGANIZATION_NAME hariç** (tüm tipler arasında en yüksek sızıntı oranına sahip olduğu için ayrıca gösterilir):

| Dil | Çapraz n | Literal: çapraz | Literal: kontrol | Literal: fark | Fragment: çapraz | Fragment: kontrol | Fragment: fark |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| en | 38 | 0,079 | 0,252 | -0,173 | 0,158 | 0,251 | -0,093 |
| tr | 44 | 0,159 | 0,149 | +0,010 | 0,386 | 0,141 | +0,245 |
| zh | 81 | 0,049 | 0,403 | -0,353 | 0,173 | 0,403 | -0,230 |

**Tipe göre literal sızıntı oranı** (tüm değerler, çapraz ve kontrol; sızıntı/n):

| Tip | en | tr | zh |
|:---|:---:|:---:|:---:|
| PERSON_NAME | 0,335 (77/230) | 0,047 (9/190) | 0,971 (369/380) |
| LOCATION | 0,872 (164/188) | 0,676 (142/210) | 0,979 (426/435) |
| ORGANIZATION_NAME | 0,912 (197/216) | 0,932 (179/192) | 1,000 (355/355) |
| EMAIL_ADDRESS | 0,000 (0/178) | 0,000 (0/209) | 0,000 (0/424) |
| PHONE_NUMBER | 0,000 (0/184) | 0,000 (0/213) | 0,000 (0/428) |
| IBAN | 0,000 (0/204) | 0,000 (0/186) | 0,000 (0/378) |

Sayıların okunuşu:

- Sızıntıyı, chunk sınırları değil, dedektörün maskelemediği değerler belirler. İçinde sınır bulunmayan kontrol değerleri bile literal olarak 0,271 ile 0,492 arasında sızar. Sızan tipler kişi adları, konumlar ve kurum adlarıdır; örneğin Çince kişi adları 380 değerin 369'unda sızar.
- Bu sentetik değerlerde ve üretilen biçimlerde e-posta, telefon ve IBAN değerleri hiçbir zaman literal olarak sızmaz. Gerçek dünyadaki biçimler farklı olabilir ve bu ölçümün kapsamında değildir. Bu üç tipteki fragment isabetleri toplamda en'de 566 değerin 3'ü, tr'de 608'in 10'u, zh'de 1.230'un 10'udur.
- Chunk sınırlarının tutarlı bir etkisi yoktur. en ve zh'de çapraz değerler kontrol değerlerinden daha az sızar. tr'de ise daha fazla sızar: 58 çapraz değerde +0,260 fragment farkı, ORGANIZATION_NAME hariç tutulduğunda 44 değerde +0,245. tr farkının nedeni araştırılmadı.

### Yeniden üretme

`packages/api` dizininden çalıştırın. Her embedder yerel Hugging Face önbelleğinde bulunmalıdır; `HF_HUB_OFFLINE=1` çalıştırmayı çevrimdışı tutar ve sabitlenmiş embedder yoksa tam çalıştırma atlanır.

```bash
cd packages/api
HF_HUB_OFFLINE=1 pytest tests/benchmark_retrieval.py -k smoke -s -q
HF_HUB_OFFLINE=1 SEPTUM_BENCHMARK_OUT=/absolute/path/run.json pytest tests/benchmark_retrieval.py -k full_retrieval -s -q
```

| Değişken | Varsayılan | Anlamı |
|:---|:---|:---|
| `SEPTUM_BENCHMARK_EMBEDDING_MODEL` | üretim MiniLM | Embedding model kimliği; `septum_core` içinde sabitlenmiş olmalıdır |
| `SEPTUM_BENCHMARK_CHUNKER` | `pipeline` | `pipeline`, `structured` ya da `sliding` |
| `SEPTUM_BENCHMARK_RETRIEVERS` | `dense,bm25,hybrid,auto_rag` | Virgülle ayrılmış liste |
| `SEPTUM_BENCHMARK_QUERY_MODE` | `both` | `raw`, `sanitized` ya da `both` |
| `SEPTUM_BENCHMARK_LATENCY_REPEATS` | `5` | Sorgu başına ölçülmüş tekrar sayısı |
| `SEPTUM_BENCHMARK_OUT` | tanımsız | JSON sonuç dosyasının yolu |

Birden fazla yapılandırma, ilkine göre bir delta tablosuyla:

```bash
./scripts/benchmark_retrieval.sh
./scripts/benchmark_retrieval.sh "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2,sliding"
```

Betik, her yapılandırma için `benchmark_runs/retrieval/<config>.json` ve bir `.log` dosyası yazar. Kayıtlı taban ölçüm `packages/api/tests/retrieval_eval/baseline_minilm.json` içinde commit'lenmiştir; yeni çalıştırmalar rapor CLI'ı ile bu dosyayla karşılaştırılabilir:

```bash
cd packages/api
python -m tests.retrieval_report base.json other.json
```

Rapor, farklı şema sürümüne ya da değerlendirme digest'ine sahip çalıştırmaları reddeder; çünkü bunlar farklı soruları ölçer.

### Sınırlamalar

- Belgeler ve sorular sentetiktir ve elle yazılmıştır. Gerçek belgeler daha uzun, daha gürültülü ve başka konularda olur.
- Örneklem küçüktür: dil başına 43 cevaplanabilir soru ve 11 olumsuz soru. %95 aralıklar geniştir; özellikle auto-RAG yanlış erişiminde.
- Dense, bm25 ve hybrid için küme Recall@10'da doygunluğa ulaşır (her satırda 1,000); bu nedenle ayırt edici metrikler Recall@5, MRR ve nDCG@10'dur. Dense, bm25 ve hybrid, Recall@5 ve MRR'de %95 CI'ları içinde örtüşür; aralarında fark varmış gibi okunmamalıdır.
- Gecikme tek makineden ve tek çalıştırmadan gelir. Diğer donanımlar hakkında bir şey söylemez.
- E-posta, telefon ve IBAN için fragment isabetleri elle doğrulanmamıştır.
- Han, kana ve Hangul için fragment kontrolü bir sezgiseldir. Kısmi isim eşleşmelerini fazla ya da eksik sayabilir.
- Temel ölçüm tek embedder ve tek parçalayıcı kapsar. Diğer yapılandırmalar burada raporlanmamıştır.

---

**Ek kaynaklar:**

- Benchmark test dosyası: [`packages/api/tests/benchmark_detection.py`](../packages/api/tests/benchmark_detection.py)
- Erişim benchmark test dosyası: [`packages/api/tests/benchmark_retrieval.py`](../packages/api/tests/benchmark_retrieval.py)
- Erişim benchmark taban kaydı (MiniLM çalıştırması): [`packages/api/tests/retrieval_eval/baseline_minilm.json`](../packages/api/tests/retrieval_eval/baseline_minilm.json)
- Regülasyon bazlı tanıyıcı paketleri: [`packages/core/septum_core/recognizers/`](../packages/core/septum_core/recognizers/)
- Ulusal kimlik doğrulayıcıları (Luhn, Verhoeff, ISO 7064, TCKN mod-10/mod-11, CPF, NRIC): [`packages/core/septum_core/national_ids/`](../packages/core/septum_core/national_ids/)
- Her varlık tipinin hukuki dayanağı: [`REGULATION_ENTITY_SOURCES.md`](../packages/core/docs/REGULATION_ENTITY_SOURCES.md)
- Microsoft Presidio: [microsoft/presidio](https://github.com/microsoft/presidio) · değerlendirme çerçevesi: [microsoft/presidio-research](https://github.com/microsoft/presidio-research)
- XLM-RoBERTa makalesi: [Conneau vd., *Unsupervised Cross-lingual Representation Learning at Scale* (ACL 2020)](https://aclanthology.org/2020.acl-main.747/)
- CoNLL-2003 shared task makalesi: [Tjong Kim Sang & De Meulder, 2003](https://aclanthology.org/W03-0419/)
- Few-NERD makalesi: [Ding vd., *Few-NERD: A Few-shot Named Entity Recognition Dataset* (ACL 2021)](https://aclanthology.org/2021.acl-long.248/)
- Ai4Privacy veri kümesi kartı: [ai4privacy/pii-masking-300k](https://huggingface.co/datasets/ai4privacy/pii-masking-300k)
- Regülasyonların birincil kaynakları: [GDPR](https://gdpr-info.eu/) · [KVKK (6698 sayılı Kanun)](https://www.mevzuat.gov.tr/MevzuatMetin/1.5.6698.pdf) · [HIPAA](https://www.hhs.gov/hipaa/for-professionals/privacy/laws-regulations/index.html) · [CCPA / CPRA](https://oag.ca.gov/privacy/ccpa) · [LGPD](https://www.gov.br/anpd/pt-br) · [UK GDPR](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/) · [PIPEDA](https://www.priv.gc.ca/en/privacy-topics/privacy-laws-in-canada/the-personal-information-protection-and-electronic-documents-act-pipeda/)

---

<p align="center">
  <a href="./index.md"><strong>🏠 Ana Sayfa</strong></a>
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
