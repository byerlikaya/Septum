---
title: "Katkı Sağlama"
description: "Hata bildirme, test çalıştırma, özellik önerme ve PR gönderme rehberi."
---

# Septum'a Katkıda Bulunma

Katkıda bulunmak için ayırdığınız zaman için teşekkürler. Septum
gizlilik öncelikli bir AI ara katmanı — her katkının bu çıtayı koruması
önemli.

## Nasıl katkıda bulunabilirsiniz

- **Hata bildirimi** — yeniden üretim adımları, beklenen vs gerçek
  davranış, ortam bilgisi (OS, Docker ya da yerel, yerel çalıştırıyorsanız
  Python / Node versiyonları) ile bir issue açın.
- **Özellik önerisi** — PR yazmadan önce `proposal` etiketli bir
  issue açıp kullanım senaryosunu ve kaba API şeklini anlatın;
  yön konusunda hizalanalım.
- **Fix veya özellik gönderimi** — repoyu fork edin, `main`'den dal
  açın, `main`'e karşı pull request açın.
- **Dokümantasyon iyileştirmesi** — yazım hataları, açıklamaların
  netleştirilmesi, eksik örnekler — hepsi memnuniyetle karşılanır.
  README'ler ve `docs/` hem İngilizce hem Türkçe; yapısal eşlik
  zorunlu (aynı başlıklar, aynı sıra, aynı linkler).

## Geliştirme ortamı

```bash
git clone https://github.com/<kullanici-adin>/Septum.git
cd Septum
./dev.sh --setup     # tüm Python paketleri + frontend bağımlılıklarını kurar
./dev.sh             # api + web'i port 3000'de başlatır
```

Gereksinimler:
- Python 3.11+ (3.13'e kadar test edilmiş)
- Node.js 20+
- ffmpeg (Whisper ses ingest'i için)

İlk ziyarette kurulum sihirbazı açılır. Manuel `.env` düzenlemesi yok.

## Testleri çalıştırma

```bash
# Backend (tüm modüller)
pytest packages/*/tests -q

# Tek modül
pytest packages/core/tests/ -q
pytest packages/api/tests/test_sanitizer.py -v

# Frontend
cd packages/web && npm test
```

Testlerdeki tüm LLM çağrıları **mock** edilmelidir — gerçek bulut
istekleri CI tarafından reddedilir.

## Kod stili

- **Python:** `ruff check` + `ruff format` geçmeli. Public API'lerde
  type hint zorunlu.
- **TypeScript:** `npm run lint` + `tsc --noEmit` geçmeli.
- **Sınıf/fonksiyon/değişken isimlerinde ülke veya dil adı yok**
  (bkz. `CLAUDE.md` § Zero-Tolerance Generic Architecture).
  İstisnalar: `national_ids/` algoritmik doğrulayıcılar, mapping
  tablolarında ISO 639-1 kodları, HuggingFace model ID'leri,
  regülasyon seed açıklamaları, test dosyaları.
- **Satır içi LLM prompt yok.** Tüm prompt'lar
  `packages/api/septum_api/services/prompts.py` içindeki
  `PromptCatalog` üzerinden geçer.
- **Her şey async.** Tüm DB, dosya I/O ve LLM çağrıları async'tir.

## Commit mesajları

Emir kipinde, İngilizce, modül scope'lu conventional prefix:

```
<type>(<scope>): <açıklama>

type: feat, fix, refactor, test, docs, chore
scope: core, mcp, api, web, queue, gateway, audit
```

Örnekler:
- `feat(core): add Australia Privacy Act recognizer pack`
- `fix(api): respect rag_relevance_threshold for empty corpus`
- `docs(readme): add Docker Compose deployment note`

## Pull request süreci

1. Fork + `main`'den dal açın.
2. Değişiklik için test yazın (ya da neden teste gerek olmadığını
   açıklayın).
3. PR'ları odaklı tutun — bir mantıksal değişiklik, bir PR. İlgisiz
   başka sorunlar yakaladıysanız ikinci PR açın.
4. `CHANGELOG.md`'yi **yalnızca PR bir sonraki sürümle ship olacaksa**
   güncelleyin — aksi halde dokunmayın.
5. `./dev.sh --setup && pytest packages/*/tests -q`
   lokalde geçiyor olmalı.
6. PR'ı `main`'e karşı açın. CI backend testlerini (Python 3.13),
   modüler testleri, ruff, bandit, pip-audit, frontend jest, tsc,
   npm audit'i çalıştırır. Hepsi geçmeli.

## Regülasyon entity kaynakları

Yerleşik bir regülasyonun entity type'larını değiştiriyorsanız (seed
ya da herhangi bir recognizer paketinde),
[`packages/core/docs/REGULATION_ENTITY_SOURCES.md`](packages/core/docs/REGULATION_ENTITY_SOURCES.md)
dosyasını **aynı PR'da** hukuki kaynak (madde/fıkra/resital) ile
güncelleyin. CHANGELOG pre-commit hook'u bu eşleşmeyi zorlar.

## Güvenlik

Güvenlik issue'larını lütfen açık şekilde yazmayın.
[GitHub profilinde](https://github.com/byerlikaya) listelenen
adresten bakımcıya e-posta atın; düzeltme ve duyuruyu birlikte
planlarız.

## Lisans

Katkılar projenin kendisiyle aynı MIT lisansı altındadır. Bir pull
request açarak katkınızın bu koşullarda dağıtılabileceğini kabul
etmiş olursunuz.
