# Septum — Ekran Görüntüleri

Septum'un her ekranının görsel turu — kurulum sihirbazı, sohbet onay
akışı, varlık renklendirmeli doküman önizlemesi, ayarlar sekmeleri,
özel regülasyon kuralları ve denetim kaydı.

Yüksek seviyeli açıklamalar için [README.tr.md](../README.tr.md);
ayrıntılı özellik ve API referansı için [FEATURES.tr.md](FEATURES.tr.md)
dosyalarına bakın.

---

## Kurulum Sihirbazı

<p align="center">
  <img src="../assets/setup-wizard.gif" alt="Kurulum sihirbazı — veritabanı, cache, LLM sağlayıcı, regülasyonlar, ses modeli, admin hesabı" width="900" />
</p>

Veritabanı (SQLite ya da PostgreSQL), cache (in-memory ya da Redis),
LLM sağlayıcı (Anthropic, OpenAI, OpenRouter ya da yerel Ollama),
gizlilik regülasyonları ve ses transkripsiyon modeli — hepsi
sihirbazdan seçilir.

---

## Onay Kapısı — Makinenizden ne çıktığını tam olarak görün

<p align="center">
  <img src="../assets/chat-flow.gif" alt="Sohbet onay akışı — maskeli prompt, getirilen parçalar, hazırlanan bulut isteği ve placeholder'lardan geri yazılmış cevap" width="900" />
</p>

Her LLM çağrısından önce Septum üç paneli yan yana gösterir: sizin
yazdığınız maskeli prompt, getirilen doküman parçaları (düzenlenebilir)
ve buluta gidecek birleştirilmiş son istek. Onayladığınızda cevap
yerelde gerçek değerlerle yeniden yazılarak önünüze gelir — bu geri
yazma adımı asla bulutta yapılmaz.

---

## Varlık renklendirmeli doküman önizleme

<p align="center">
  <img src="../assets/document-preview.gif" alt="Doküman listesi ve önizleme, tespit edilen PII satır içinde renklendirilmiş" width="900" />
</p>

Tespit edilen her varlık — isim, adres, doğum tarihi, telefon, tıbbi
teşhis, kimlik — orijinal doküman üzerinde varlık tipine göre
renklendirilir. Her varlığa tıkladığınızda konumuna gidersiniz; yan
panelde her eşleşme skoru ve placeholder'ıyla listelenir.

---

## Ayarlar — 5 sekmelik tur

<table>
  <tr>
    <td width="50%" align="center">
      <b>LLM Sağlayıcı</b><br />
      <img src="../assets/14-settings-llm-provider.png" alt="LLM sağlayıcı ayarları" />
    </td>
    <td width="50%" align="center">
      <b>Gizlilik ve Sanitizasyon</b> — 3 katmanlı hat<br />
      <img src="../assets/15-settings-privacy-sanitization.png" alt="Gizlilik sanitizasyon ayarları" />
    </td>
  </tr>
  <tr>
    <td align="center">
      <b>RAG ve Hibrit Retrieval</b><br />
      <img src="../assets/16-settings-rag.png" alt="RAG ayarları" />
    </td>
    <td align="center">
      <b>Doküman Ingest</b><br />
      <img src="../assets/17-settings-ingestion.png" alt="Ingest ayarları" />
    </td>
  </tr>
  <tr>
    <td colspan="2" align="center">
      <b>Altyapı</b> — veritabanı, cache, LLM gateway<br />
      <img src="../assets/18-settings-infrastructure.png" alt="Altyapı ayarları" width="720" />
    </td>
  </tr>
</table>

---

## Özel Regülasyon Kuralları

<p align="center">
  <img src="../assets/19-regulations-custom-rules.png" alt="Özel regülasyon kuralları — regex, anahtar kelime, LLM prompt" width="900" />
</p>

17 hazır paketin yanında kendi tanıyıcılarınızı tanımlayın. Üç yöntem:
regex örüntüsü, anahtar kelime listesi veya LLM-prompt tabanlı tespit.
Politika birleştirme kuralları yine geçerlidir — en kısıtlayıcı kural
kazanır.

---

## Denetim Kaydı

<p align="center">
  <img src="../assets/23-audit-trail.png" alt="Denetim kaydı" width="900" />
</p>

Salt-ekleme uyumluluk günlüğü ve varlık tespit metrikleri. Denetim
olaylarında ham PII yoktur — yalnızca varlık tipi, adet, regülasyon id
ve zaman damgası tutulur. `/api/audit/export` üzerinden JSON / CSV /
Splunk HEC export alabilirsiniz.
