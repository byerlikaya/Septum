<p align="center">
  <img src="septum_logo.png" alt="Septum logosu" width="220" />
</p>

<p align="center">
  <img src="https://img.shields.io/badge/backend-FastAPI-blue" alt="Backend: FastAPI" />
  <img src="https://img.shields.io/badge/frontend-Next.js%2016-black" alt="Frontend: Next.js 16" />
  <img src="https://img.shields.io/badge/testler-pytest-informational" alt="Testler: pytest" />
  <a href="https://codecov.io/gh/byerlikaya/Septum">
    <img src="https://codecov.io/gh/byerlikaya/Septum/branch/main/graph/badge.svg" alt="Kapsam: Codecov" />
  </a>
  <img src="https://img.shields.io/badge/odak-Gizlilik--Öncelikli-green" alt="Odak: Gizlilik-Öncelikli" />
  <a href="README.md">
    <img src="https://img.shields.io/badge/lang-EN-blue" alt="English README" />
  </a>
  <br />
  <img src="https://img.shields.io/badge/guvenlik_taramasi-passing_(2026--03--10)-brightgreen" alt="Güvenlik taraması: passing (2026-03-10)" />
  <img src="https://img.shields.io/badge/bagimlilikler-denetim_temiz-brightgreen" alt="Bağımlılıklar: denetim temiz" />
</p>

## Septum — Gizlilik Odaklı Yapay Zekâ Asistanı

Septum, kurumların **kendi verilerini** büyük dil modelleri (LLM) ile kullanırken gizliliği koruması için tasarlanmış bir **ara katman (middleware)** ve web uygulamasıdır.

Kısaca:
- Dokümanlarınızı (PDF, Word, Excel, görsel, ses vb.) yüklersiniz.
- Septum, içindeki kişisel verileri (PII) **yerelde maskeleyip anonimleştirir**.
- Sorularınızı bu anonimleştirilmiş veriler üzerinden LLM’e sorar.
- Gelen cevabı yine yerelde, gerçek isimler ve değerlerle **geri yerine koyarak** size gösterir.

Buluta giden hiçbir içerik ham hâliyle veya doğrudan tanımlayıcı kişisel veri içerecek şekilde çıkmaz.

---

## Ne İşe Yarar?

- **Güvenli kurumsal doküman sorgulama**  
  - Politikalar, sözleşmeler, müşteri dosyaları, sağlık kayıtları, insan kaynakları dokümanları gibi hassas içerikleri LLM ile sorgulamanızı sağlar.
  - LLM cevabı üretirken gerçek kimlik bilgilerini görmez; sadece maske (ör. `[PERSON_1]`, `[EMAIL_2]`) görür.

- **Regülasyon uyumlu veri paylaşımı**  
  - GDPR, KVKK, HIPAA gibi regülasyonlara tabi verileri buluta göndermeden önce anonimleştirerek **uyum riskini azaltır**.

- **İç bilgiye dayalı akıllı asistan**  
  - Kendi dokümanlarınızı vektör veritabanına (RAG) gömerek, şirket içi arama ve soru–cevap deneyimi oluşturur.

Özetle: Septum, “LLM’e doküman verelim ama kişisel veriler dışarı çıkmasın” diyen kurumlar için bir **güvenlik katmanı** sağlar.

---

## Nerelerde Kullanılır?

- **Finans**  
  Müşteri sözleşmeleri, kredi dosyaları, iç prosedürler üzerinde arama ve özetleme yaparken PII’yi korumak için.

- **Sağlık**  
  Hasta dosyaları, epikrizler, laboratuvar raporları gibi **sağlık verilerini** anonimleştirip hekim destek araçlarında kullanmak için.

- **Hukuk & Uyumluluk**  
  Sözleşmeler, dava dosyaları, KVKK/GDPR dokümanları üzerinde arama ve analiz yaparken isim, TC, adres vb. bilgileri dışarı çıkarmadan çalışmak için.

- **İK ve Operasyon**  
  Personel dosyaları, performans raporları, maaş verileri gibi hassas bilgilerle çalışan iç asistanlar geliştirmek için.

Her yerde ortak amaç: **LLM gücünden faydalanırken, kişisel veriyi kurum sınırları içinde ve şifreli tutmak.**

---

## Başlıca Özellikler

- **Yerel PII Koruması**
  - Ham kişisel veriler (isim, TC, adres, e‑posta vb.) makineyi terk etmez.
  - Dosyalar diskte şifreli tutulur, çözme işlemi sadece görüntüleme anında ve bellek içinde yapılır.

- **Çoklu Regülasyon Desteği**
  - GDPR, KVKK, CCPA, HIPAA, LGPD vb. pek çok regülasyon için hazır paketler.
  - Birden fazla regülasyonu aynı anda etkinleştirip “en kısıtlayıcı” maskeleme politikasını uygulama.

- **Kullanıcı Tanımlı Kurallar**
  - “Şu regex’e uyan her şeyi maskele”, “Bu anahtar kelimeleri gördüğünde sakla”, “Maaşla ilgili her ifadeyi yakala” gibi özel kurallar tanımlayabilirsiniz.

- **Çok Formatlı Doküman Desteği**
  - PDF, Office dosyaları, görseller (OCR), ses kayıtları (transkript), e‑postalar ve daha fazlası.

- **Onay Mekanizmalı Chat**
  - LLM’e gönderilmeden önce, hangi bilgilerin paylaşılacağını bir özet ekranında görür ve onay/vermezsiniz.

---

## Septum’u Nasıl Kullanırım? (Kısa Senaryo)

1. **Dokümanları yükleyin**  
   Dokümanlar sayfasından veya Sohbet ekranındaki yan paneldeki yükleme alanını kullanarak PDF, Word, Excel, görsel ya da ses dosyalarınızı Septum’a yüklersiniz.

2. **Septum veriyi işler ve anonimleştirir**  
   - Dosyanın türünü, dilini ve içindeki kişisel verileri otomatik tespit eder.
   - Kişisel verileri yerelde maskeler ve anonimleştirilmiş hâlini arama için hazırlar.

3. **Sorular sorun**  
   - “Şu sözleşmede iptal şartları neler?”,  
     “Bu müşterinin hangi ürünleri var?”,  
     “Son 6 ayda X ile ilgili hangi vaka kayıtları oluşturulmuş?” gibi sorular sorarsınız.  
   - Sohbet ekranından bir doküman yüklediğinizde, bu doküman varsayılan olarak seçilir ve sorularınız doğrudan bu doküman üzerinden çalışır.

4. **Gönderilmeden önce onay verin**  
   - Buluta gidecek anonimleştirilmiş içerik size gösterilir.
   - Onay verirseniz LLM’e sadece maskeli metin gönderilir.

5. **Cevabı gerçek verilerle görün**  
   - LLM cevabı döndüğünde, Septum kendi içinde placeholder’ları gerçek değerlerle eşleştirerek size anlamlı, okunabilir sonuç sunar.

---

## Kısa Teknik Özet

- **Backend**: Python + FastAPI  
  - Doküman işleme, anonimleştirme, şifreleme ve LLM entegrasyonu burada çalışır.
  - Tüm veri işleme ve PII koruma mantığı sunucu tarafındadır.

- **Frontend**: Next.js 16 + React 19  
  - Chat, doküman yönetimi, ayarlar ve regülasyon ekranlarını sunan web arayüzü.
  - Backend ile HTTP ve SSE (stream) üzerinden haberleşir.

Bu teknik detaylar geliştiriciler içindir; son kullanıcı genelde sadece web arayüzünü kullanır.

---

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

