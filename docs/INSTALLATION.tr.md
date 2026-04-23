---
title: "Kurulum Rehberi"
description: "Hızlı başlangıç, desteklenen topolojiler, ilk açılış sihirbazı, güncelleme ve sorun giderme."
---

# Septum — Kurulum Rehberi

<p align="center">
  <a href="../README.tr.md"><strong>🏠 Ana Sayfa</strong></a>
  &nbsp;·&nbsp;
  <strong>🚀 Kurulum</strong>
  &nbsp;·&nbsp;
  <a href="BENCHMARK.tr.md"><strong>📈 Benchmark</strong></a>
  &nbsp;·&nbsp;
  <a href="FEATURES.tr.md"><strong>✨ Özellikler</strong></a>
  &nbsp;·&nbsp;
  <a href="ARCHITECTURE.tr.md"><strong>🏗️ Mimari</strong></a>
  &nbsp;·&nbsp;
  <a href="DOCUMENT_INGESTION.tr.md"><strong>📊 Doküman İşleme</strong></a>
  &nbsp;·&nbsp;
  <a href="SCREENSHOTS.tr.md"><strong>📸 Ekran Görüntüleri</strong></a>
</p>

---

## İçindekiler

- [⚡ Hızlı başlangıç](#-hızlı-başlangıç)
- [Sistem gereksinimleri](#sistem-gereksinimleri)
- [Kurulum varyantları](#kurulum-varyantları)
  - [Tam yerel yığın (önerilen)](#1-tam-yerel-yığın--önerilen)
  - [Tek container — demo](#2-tek-container--demo)
  - [Yalnız air-gapped bölge](#3-yalnız-air-gapped-bölge)
  - [Yalnız internet-facing bölge](#4-yalnız-internet-facing-bölge)
  - [Kaynaktan geliştirici kurulumu](#5-kaynaktan-geliştirici-kurulumu)
- [İlk açılış — kurulum sihirbazı](#i̇lk-açılış--kurulum-sihirbazı)
- [LLM sağlayıcıları](#llm-sağlayıcıları)
- [Veri kalıcılığı ve volume'lar](#veri-kalıcılığı-ve-volumelar)
- [Güncelleme](#güncelleme)
- [Sorun giderme](#sorun-giderme)
- [Kaldırma](#kaldırma)

---

## ⚡ Hızlı başlangıç

Çalışan bir Septum'a giden en kısa yol. Üç komut, beş dakika — paketi içindeki Ollama ile birlikte local-first bir AI ara katmanı.

```bash
git clone https://github.com/byerlikaya/Septum.git && cd Septum
cp .env.example .env && $EDITOR .env    # POSTGRES_PASSWORD ve REDIS_PASSWORD'ü doldurun
docker compose up
```

Tarayıcınızda **http://localhost:3000** adresini açın; kurulum sihirbazı gerisini halleder.

Compose yığını, tek bir makinede PostgreSQL, Redis, Ollama, FastAPI backend ve Next.js paneli birlikte ayağa kaldırır. İlk açılışta Docker Hub'dan image'lar ve Ollama'nın varsayılan modeli indirilir; sonraki açılışlar saniyeler içinde tamamlanır. Ham PII hiçbir zaman makineden dışarı çıkmaz — tespit, maskeleme ve LLM çıkarımının tamamı yerelde yürür.

---

## Sistem gereksinimleri

| Kaynak | Asgari | Önerilen |
|---|---|---|
| CPU | 2 çekirdek (x86-64 veya arm64) | 4 çekirdek ve üzeri |
| RAM | 6 GB boş | 16 GB (Ollama'nın 7B-sınıfı model tutması için) |
| Disk | 12 GB boş | 30 GB (model + doküman indeksleri zamanla büyür) |
| Docker | Desktop 4.30+ / Engine 24+ (Compose v2 dahil) | Son kararlı sürüm |
| OS | macOS 13+, WSL2 ile Windows 10/11, güncel bir Linux dağıtımı | — |

**Platform notları.**

- **Apple Silicon (M1/M2/M3/M4)** — tam destekli. Ollama Metal hızlandırmasını kendiliğinden devreye alır; ek bir bayrağa ihtiyacınız yoktur. Multi-arch Docker image'ları arm64 varyantıyla birlikte yayınlanır.
- **NVIDIA GPU (Linux amd64)** — opsiyoneldir. `byerlikaya/septum` ve `byerlikaya/septum-api` image'larının `-gpu` tag'leri CUDA hızlandırmalı PyTorch ile gelir (OCR, Whisper ve embedding ölçülebilir biçimde hızlanır). Ollama, container'dan bir GPU gördüğünde bu cihazı otomatik kullanır.
- **Windows** — WSL2 gereklidir; native Windows container'ları desteklenmez. Rehber boyunca görülen yollar ve volume örnekleri POSIX kabuğuna göredir; komutları WSL terminalinden çalıştırın.

---

## Kurulum varyantları

Desteklenen beş topoloji vardır. Dağıtım şeklinize uyan varyantı seçin.

### 1. Tam yerel yığın — **önerilen**

Tek bir host üzerinde tüm modüllerin yanı sıra Ollama, PostgreSQL ve Redis. Varsayılan `docker-compose.yml` bu topolojiyi üretir.

```bash
git clone https://github.com/byerlikaya/Septum.git && cd Septum
cp .env.example .env
# .env dosyasını açın ve POSTGRES_PASSWORD + REDIS_PASSWORD değerlerini doldurun (zorunlu)

docker compose up           # ön planda — ilk açılış loglarını izleyin
# ya da
docker compose up -d        # arka planda — terminali serbest bırakır

# İlk açılışta paketli Ollama'ya varsayılan bir model çekin
docker compose exec ollama ollama pull llama3.2:3b
```

**http://localhost:3000** adresine gidin. Sihirbazın akışı için [İlk açılış](#i̇lk-açılış--kurulum-sihirbazı) bölümüne bakın.

Ollama olmaksızın çalıştırmak isterseniz (yalnız bulut sağlayıcı kullanılan dağıtımlar):

```bash
docker compose -f docker-compose.yml -f docker-compose.no-ollama.yml up
```

### 2. Tek container — **demo**

Tek bir container, SQLite, harici servis yok. Bir dizüstü bilgisayarda Septum'u hızlıca denemenin en pratik yolu; satış demoları ve küçük ölçekli kurulumlar için idealdir.

```bash
docker run --name septum \
  --add-host=host.docker.internal:host-gateway \
  -p 3000:3000 \
  -v septum-data:/app/data \
  -v septum-uploads:/app/uploads \
  -v septum-anon-maps:/app/anon_maps \
  -v septum-vector-indexes:/app/vector_indexes \
  -v septum-bm25-indexes:/app/bm25_indexes \
  -v septum-models:/app/models \
  byerlikaya/septum
```

Standalone varyantı Ollama'yı paketinde taşımaz. Ya host'a Ollama kurun (`brew install ollama && ollama serve`) ve sihirbaza `http://host.docker.internal:11434` adresini verin, ya da Ollama'yı atlayıp bulut sağlayıcısıyla (Anthropic / OpenAI / OpenRouter) ilerleyin.

**Standalone'u şu durumlarda seçin**: mümkün olan en hızlı "hemen deneyeyim" akışını istiyorsanız, korpusunuz küçükse ve modüler bölge ayrımına ihtiyaç duymuyorsanız.

**Şu durumlarda seçmeyin**: air-gap ayrımı, çok kullanıcılı ölçek ya da Septum'un tüm özellik setine (semantik PII katmanı + Otomatik RAG yönlendirme — her ikisi de Ollama ister) ihtiyacınız varsa.

### 3. Yalnız air-gapped bölge

Yalnızca air-gapped bölgeyi (api + web + PostgreSQL + Redis) internete çıkışı olmayan bir host üzerinde çalıştırır. Bulut LLM çağrıları, gateway'in koştuğu ayrı bir internete açık host üzerinden yapılır.

```bash
cp .env.example .env
# POSTGRES_PASSWORD ve REDIS_PASSWORD değerlerini doldurun

docker compose -f docker-compose.airgap.yml up -d
```

Bu topoloji `USE_GATEWAY_DEFAULT=true` kullanır; her LLM çağrısı HTTP yerine `septum-queue` (Redis Streams) üzerinden yönlendirilir. Bölge sınırını yalnızca maskelenmiş metin aşar. İnternete açık hostu varyant 4 ile birlikte kurun.

### 4. Yalnız internet-facing bölge

Yalnızca gateway ve audit modüllerini, bulut LLM sağlayıcılarına ulaşabilen bir host üzerinde çalıştırır. Redis'ten maskelenmiş istekleri tüketir ve Anthropic / OpenAI / OpenRouter'a iletir.

```bash
cp .env.example .env
# REDIS_PASSWORD'ü doldurun (air-gapped host ile aynı değer; VPN / özel ağ üzerinden paylaşın)
# Sağlayıcı anahtarlarını girin: ANTHROPIC_API_KEY, OPENAI_API_KEY vb.

docker compose -f docker-compose.gateway.yml up -d
```

Gateway image'ı güvenlik invariantı gereği `septum-core`'u içermez — yalnızca placeholder ile maskelenmiş metni görür ve maskelenmiş cevapları geri yayınlar. Audit sidecar'ı her isteği varlık tipi, sayı ve regülasyon id'leriyle loglar (ham PII asla yer almaz) ve `/api/audit/export` uç noktasını SIEM entegrasyonu için açar.

**Air-gapped + internet-facing birlikte**: iki Docker host; her ikisinden de (ideal olarak VPN ya da özel alt ağ üzerinden) erişilebilen ortak bir Redis. İki hostta `REDIS_PASSWORD` değeri aynı olmalı. Septum'un şifreleme invariantı Redis'in yalnızca placeholder ile maskelenmiş payload'ları gördüğünü garanti eder.

### 5. Kaynaktan geliştirici kurulumu

Geliştirme, hata ayıklama veya özel build'ler için. Üretim ortamı için önerilmez; bunun yerine bir compose varyantı kullanın.

```bash
git clone https://github.com/byerlikaya/Septum.git && cd Septum
./dev.sh --setup        # altı Python paketini ve npm bağımlılıklarını kurar
./dev.sh                # api ile web'i port 3000 üzerinde başlatır
```

Gereksinimler: Python 3.11+ (3.13 üzerinde doğrulandı), Node.js 20+, ffmpeg (Whisper ses akışı için). Bootstrap script'i ilk açılışta yeni bir şifreleme anahtarı ve JWT gizli anahtarıyla `config.json` üretir; yolu değiştirmek için `SEPTUM_CONFIG_PATH` ortam değişkenini kullanabilirsiniz.

Test setini çalıştırın:

```bash
pytest packages/*/tests -q        # 7 modülün tamamı
cd packages/web && npm test       # frontend Jest suite
```

---

## İlk açılış — kurulum sihirbazı

Compose varyantlarından biri açılışı tamamladığında **http://localhost:3000** adresine gidin. Sihirbaz ilk kurulumda bir kez çalışır ve backend'in ihtiyaç duyduğu her şeyi yapılandırır.

| Adım | Ne yaparsınız | Notlar |
|---|---|---|
| **1. Veritabanı** | SQLite (varsayılan) veya PostgreSQL seçin, bağlantıyı test edin | Compose varyantları PostgreSQL'i hazır getirir; standalone SQLite'a düşer |
| **2. Önbellek** | In-memory (varsayılan) veya Redis seçin, bağlantıyı test edin | Compose Redis'i hazır getirir; standalone in-memory kullanır |
| **3. LLM sağlayıcı** | Ollama (yerel, tam yığında paketli) / Anthropic / OpenAI / OpenRouter | Anahtar edinimi için [LLM sağlayıcıları](#llm-sağlayıcıları) bölümüne bakın |
| **4. Regülasyonlar** | İhtiyaç duyduğunuz paketleri (genelde GDPR + kendi ülkeniz) seçin | Daha sonra Ayarlar → Regülasyonlar sekmesinden değiştirilebilir |
| **5. Ses modeli** | Whisper boyutu (tiny / base / small / medium / large) | Ses dosyası yüklemeyecekseniz atlayın; ilk ses yüklemesinde tembel indirilir |
| **6. Admin hesabı** | İlk yönetici için e-posta ve şifre | Tek admin burada oluşturulur; diğer hesaplar panelden eklenir |

Sihirbaz tamamlandığında `config.json` yazılır, admin kullanıcı oluşturulur ve panele yönlendirilirsiniz. Sihirbaz tek seferlik bir akıştır; kurulum bittikten sonra `/api/setup/*` uçlarına yapılan istek 403 döner.

---

## LLM sağlayıcıları

Septum, aşağıdaki sağlayıcıların herhangi bir kombinasyonunu kullanabilir:

| Sağlayıcı | Anahtarı nereden alırsınız | Ne zaman kullanılır |
|---|---|---|
| **Ollama** (yerel) | Tam yığında paketli; standalone için [ollama.com](https://ollama.com) | Semantik PII katmanı, Otomatik RAG sınıflandırıcısı, ücretsiz yerel sohbet |
| **Anthropic** | [console.anthropic.com](https://console.anthropic.com) | Claude Opus / Sonnet / Haiku — en kaliteli yanıtlar |
| **OpenAI** | [platform.openai.com](https://platform.openai.com) | GPT-4, GPT-4 Turbo, GPT-4o |
| **OpenRouter** | [openrouter.ai](https://openrouter.ai) | Tek anahtarla 100'den fazla modele birleşik API |

**Önerilen Ollama modelleri:**

- `llama3.2:3b` — 2 GB, hızlı; Otomatik RAG sınıflandırması için yeterli
- `aya-expanse:8b` — 4.7 GB, semantik PII tespiti için **önerilen**; benchmark varsayılanı
- `qwen2.5:14b` — 8.4 GB; RAM'iniz yetiyorsa daha yüksek kalite

İstediğiniz zaman ek model indirebilirsiniz:

```bash
docker compose exec ollama ollama pull aya-expanse:8b
docker compose exec ollama ollama list     # indirilenleri listeler
```

Sohbet için ve semantik tespit için kullanılacak modeller, sihirbazdan sonra Ayarlar → LLM sekmesinden ayrı ayrı seçilebilir.

---

## Veri kalıcılığı ve volume'lar

Compose yığını, container yeniden başlatmaları ve image yükseltmeleri arasında veri kaybı yaşanmaması için isimlendirilmiş Docker volume'ları tanımlar:

| Volume | Container içindeki yol | İçerik |
|---|---|---|
| `septum-data` | `/app/data` | SQLite veritabanı (kullanılıyorsa), `config.json`, şifreleme anahtarları |
| `septum-uploads` | `/app/uploads` | Yüklenen orijinal dosyalar, AES-256-GCM ile şifreli |
| `septum-anon-maps` | `/app/anon_maps` | Doküman başına PII placeholder eşlemeleri, şifreli |
| `septum-vector-indexes` | `/app/vector_indexes` | FAISS embedding'leri |
| `septum-bm25-indexes` | `/app/bm25_indexes` | BM25 kelime indeksleri |
| `septum-models` | `/app/models` | Önbelleklenmiş HuggingFace ve Whisper modelleri |
| `septum-postgres` | `/var/lib/postgresql/data` | PostgreSQL veri dizini (yalnızca compose) |
| `septum-redis` | `/data` | Redis AOF/RDB (yalnızca compose) |
| `septum-ollama` | `/root/.ollama` | İndirilmiş Ollama modelleri |

**Yedekleme.** Tutarlı bir yedek iki parçadan oluşur: bir PostgreSQL dump ve şifreli volume'lar (uploads + anon-maps + vector/BM25 indeksleri). Şifreleme anahtarı `septum-data/config.json` dosyasındadır — bu anahtarı kaybederseniz şifreli tüm volume'lar okunamaz hale gelir.

```bash
# Postgres dump'ı al
docker compose exec -T postgres pg_dump -U septum septum > septum-$(date +%F).sql

# İsimlendirilmiş volume'ları tar'la
for v in data uploads anon-maps vector-indexes bm25-indexes models; do
  docker run --rm -v septum-$v:/src -v "$PWD":/dst alpine \
    tar czf /dst/septum-$v-$(date +%F).tar.gz -C /src .
done
```

**Geri yükleme** aynı akışın tersidir: yığını durdurun, mevcut volume'ları `docker volume rm` ile silin, arşivleri yeniden açıp yığını başlatın.

---

## Güncelleme

Septum semver'a uyar. Minor sürümler yapılandırmayı bozmaz; major sürümler [Changelog](../CHANGELOG.md) içinde açık göç notlarıyla duyurulur.

```bash
git pull                                    # compose dosyası ara ara değişir
docker compose pull                         # Docker Hub'dan yeni image tag'lerini çeker
docker compose up -d                        # container'ları yeni image'larla yeniden yaratır
docker compose logs -f api                  # migration'ı izleyin
```

Alembic migration'ları api boot anında otomatik uygulanır. Bir migration başarısız olursa api container'ı açıklayıcı bir hata mesajıyla sonlanır; log'u inceleyip sebebi çözdükten sonra `up -d`'yi tekrarlayın.

Standalone image için:

```bash
docker stop septum && docker rm septum
docker pull byerlikaya/septum
# Başlangıçta kullandığınız `docker run ...` komutunu aynen tekrarlayın
```

Stop / rm / run döngüsü boyunca veriler korunur çünkü volume'lar isimlendirilmiş volume'lardır.

---

## Sorun giderme

**Sihirbaz "Redis / PostgreSQL bağlantısı sınanıyor" adımında asılı kalıyor.** Compose dosyası `POSTGRES_PASSWORD` ve `REDIS_PASSWORD` değerlerini zorunlu tutar; `.env` boşsa veri container'ları hiç başlamamıştır. `docker compose logs postgres redis` ile bakın ve değişkenleri doldurup yeniden başlatın.

**Ollama adımı "connection refused" hatası veriyor.** Tam yığın: Ollama container'ı ilk modeli indiriyor olabilir; 30-60 saniye bekleyip tekrar deneyin. Standalone: Ollama paketli değildir — ya host'a kurun (`brew install ollama`) ve sihirbaza `http://host.docker.internal:11434` adresini verin, ya da bulut sağlayıcıya geçin.

**Port 3000 kullanımda.** Makinenizde başka bir şey bu portu tutuyor. Ya o süreci durdurun ya da Septum'un bağlanma portunu değiştirin: bir compose override'ında `-p 3001:3000` kullanın veya `docker-compose.yml` içindeki `ports:` satırını düzenleyin.

**Paralel yükleme sırasında `disk I/O error`.** SQLite'ın WAL'ı tek yazıcı kilidine sahiptir; havuz 50 bağlantıya kadar sığar ama yoğun paralel OCR + Whisper yükü yine de doyurabilir. PostgreSQL'e geçin (compose varyantları bunu zaten yapar) ya da Ayarlar → İngest sekmesinden eşzamanlılık üst sınırını düşürün.

**İlk ses yüklemesinde Whisper modeli indirme zaman aşımına uğruyor.** Model tembel indirilir ve büyük boyutlarda dakikalar sürebilir. İlk gerçek yüklemeden önce modeli Ayarlar → İngest sekmesinden önceden ısıtın veya `docker compose exec api python -c "import whisper; whisper.load_model('base')"` çalıştırın.

**Docker Desktop "yetersiz kaynak" diyor.** Docker Desktop → Ayarlar → Kaynaklar altından VM belleğini en az 8 GB'a çıkarın. Ollama + Presidio + Whisper birlikte RAM'i iştahla tüketir.

**Başka bir şey.** İlgili log akışına bakın:

```bash
docker compose logs -f api       # backend, pipeline, auth, audit
docker compose logs -f web       # Next.js build + runtime
docker compose logs -f ollama    # model indirme, GPU tespiti
docker compose logs -f postgres  # veritabanı init / migration
```

Sebep hâlâ ortaya çıkmıyorsa hata loguyla, kullandığınız compose varyantıyla ve OS + Docker sürüm bilgisiyle birlikte [GitHub issue](https://github.com/byerlikaya/Septum/issues) açın.

---

## Kaldırma

Temiz kaldırma:

```bash
docker compose down -v           # container'ları durdurur, isimlendirilmiş volume'ları siler
docker rmi $(docker images 'byerlikaya/septum*' -q)
```

Kritik bayrak `-v`'dir; bu olmadan volume'lar kalır ve sonraki `docker compose up` eski verilerinizi diriltir. "Her ihtimale karşı" tutmak istiyorsanız `-v` olmadan çalıştırın ve önce volume'ları yedekleyin ([Veri kalıcılığı](#veri-kalıcılığı-ve-volumelar) bölümüne bakın).

Standalone image için:

```bash
docker stop septum && docker rm septum
docker volume rm septum-data septum-uploads septum-anon-maps \
                 septum-vector-indexes septum-bm25-indexes septum-models
docker rmi byerlikaya/septum
```

Kaynaktan kurulum için klonladığınız dizini ve Septum'un (varsa) `~/.cache/septum` altına yazdığı Python / npm önbelleklerini silmek yeterlidir.

---

<p align="center">
  <a href="../README.tr.md"><strong>🏠 Ana Sayfa</strong></a>
  &nbsp;·&nbsp;
  <strong>🚀 Kurulum</strong>
  &nbsp;·&nbsp;
  <a href="BENCHMARK.tr.md"><strong>📈 Benchmark</strong></a>
  &nbsp;·&nbsp;
  <a href="FEATURES.tr.md"><strong>✨ Özellikler</strong></a>
  &nbsp;·&nbsp;
  <a href="ARCHITECTURE.tr.md"><strong>🏗️ Mimari</strong></a>
  &nbsp;·&nbsp;
  <a href="DOCUMENT_INGESTION.tr.md"><strong>📊 Doküman İşleme</strong></a>
  &nbsp;·&nbsp;
  <a href="SCREENSHOTS.tr.md"><strong>📸 Ekran Görüntüleri</strong></a>
</p>
