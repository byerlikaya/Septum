# septum-mcp

> 🇬🇧 [English version](README.md)

[Septum](https://github.com/byerlikaya/Septum)'un gizlilik öncelikli PII maskeleme hattını **MCP uyumlu her istemciye** açan Model Context Protocol sunucusu. MCP, açık ve sağlayıcıdan bağımsız bir [spesifikasyon](https://modelcontextprotocol.io)dur; sunucu resmi Python SDK'si üzerine yazılmıştır ve üç standart taşıma katmanının tamamını destekler:

- **stdio** (varsayılan) — sunucuyu alt süreç olarak başlatan yerel istemciler için: Claude Code, Claude Desktop, Cursor, Windsurf, Zed, Cline, Continue ile LangChain / LlamaIndex MCP adaptörleri.
- **streamable-http** — uzak, container içi ya da tarayıcıya gömülü istemciler için modern HTTP taşıması. Statik bir bearer token ile güvenceye alınır.
- **sse** — eski HTTP + Server-Sent Events taşıması; henüz streamable-http'ye geçmemiş istemciler için tutulur.

Tespitin tamamı `septum-core` üzerinden **yerelde** yürür. Ham PII makineden dışarı çıkmaz. Sunucu yalnızca taşıma görevi görür: çekirdek motoru sarıp çağıranın eline altı araç bırakır.

## Araçlar

| Araç | Açıklama |
|---|---|
| `mask_text` | Metindeki PII'yi tespit eder ve maskeler. Maskelenmiş metin + `session_id` döner. |
| `unmask_response` | LLM cevabındaki placeholder'ları `session_id` üzerinden gerçek değerlere geri yazar. |
| `detect_pii` | Session tutmadan metindeki PII'yi tarar. |
| `scan_file` | Yerel bir dosyayı (`.txt`, `.md`, `.csv`, `.json`, `.pdf`, `.docx`) okur ve içeriğini tarar. |
| `list_regulations` | Yerleşik regülasyon paketlerini ve tanımladıkları varlık tiplerini listeler. |
| `get_session_map` | Bir session için `{orijinal → placeholder}` haritasını döner (yalnızca debug amaçlı). |

## Kurulum

```bash
pip install septum-mcp
```

Paket henüz `refactor/modular-architecture` dalında geliştirilirken kaynaktan:

```bash
pip install -e packages/core
pip install -e packages/mcp
```

## İstemci yapılandırması

Her MCP istemcisinin bir sunucu kaydetme yolu vardır — aşağıdaki blok Claude Code'un (`~/.claude/mcp.json`), Claude Desktop'un (macOS'ta `~/Library/Application Support/Claude/claude_desktop_config.json`), Cursor'un (Ayarlar → MCP) ve diğer pek çok istemcinin kullandığı kanonik `mcpServers` JSON biçimidir. Farklı bir yapılandırma dosyası tutan editörler (Zed, Cline, Continue, …) aynı alanlar için kendi arayüzlerini sunar: bu dokümandan ihtiyaç duyacağınız tek şey `command`, `args` ve `env` alanlarıdır.

### Yöntem A — pip ile kurulu (önerilen)

```json
{
  "mcpServers": {
    "septum": {
      "command": "septum-mcp",
      "env": {
        "SEPTUM_REGULATIONS": "gdpr,kvkk",
        "SEPTUM_LANGUAGE": "tr",
        "SEPTUM_USE_NER": "true"
      }
    }
  }
}
```

### Yöntem B — uvx (izole ortam, `pip install` gerekmez)

```json
{
  "mcpServers": {
    "septum": {
      "command": "uvx",
      "args": ["septum-mcp"],
      "env": {
        "SEPTUM_REGULATIONS": "gdpr",
        "SEPTUM_USE_NER": "false"
      }
    }
  }
}
```

### Yöntem C — repo üzerinde yerel geliştirme

```json
{
  "mcpServers": {
    "septum": {
      "command": "python",
      "args": ["-m", "septum_mcp.server"],
      "env": {
        "SEPTUM_REGULATIONS": "gdpr",
        "PYTHONPATH": "/Septum/dizininin/mutlak/yolu/packages/core:/Septum/dizininin/mutlak/yolu/packages/mcp"
      }
    }
  }
}
```

## Uzak HTTP dağıtımı

MCP istemcisi bir alt süreç başlatamadığında HTTP taşımasına geçin: web tabanlı ajanlar, tarayıcı eklentileri, paylaşılan takım sunucusu ya da container orkestrasyon dağıtımı. Sunucu aynı altı aracı sunmaya devam eder; yalnızca taşıma değişir.

### Sunucuyu çalıştırmak

```bash
# Token'ı bir kere üretin (bir secret manager'da saklayın):
openssl rand -hex 32

# Sunucuyu kimlik doğrulamalı başlatın
SEPTUM_MCP_HTTP_TOKEN=<rastgele-token> septum-mcp \
  --transport streamable-http \
  --host 0.0.0.0 \
  --port 8765
```

CLI bayrakları (`--transport`, `--host`, `--port`, `--token`, `--mount-path`) ilgili `SEPTUM_MCP_*` ortam değişkenlerini geçersiz kılar.

Token olmadığında sunucu kimlik doğrulamasını uygulamaz — bu durum açıkça (başlangıçta log'a) duyurulur ve yalnızca localhost için tasarlanmıştır.

### İstemci yapılandırması (streamable-http)

```jsonc
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

URL yolu varsayılan olarak streamable-http için `/mcp`, sse için `/sse`'dir — `--mount-path` ya da `SEPTUM_MCP_HTTP_MOUNT_PATH` ile değiştirilebilir.

### Docker

```bash
docker run -p 8765:8765 \
  -e SEPTUM_MCP_HTTP_TOKEN=<rastgele-token> \
  -e SEPTUM_MCP_HTTP_HOST=0.0.0.0 \
  septum/mcp:latest
```

Ya da docker-compose ile `docker-compose.yml` içindeki `mcp` profile'ını açın:

```bash
SEPTUM_MCP_HTTP_TOKEN=<rastgele-token> \
  docker compose --profile mcp up mcp
```

### Dağıtım notları

- **Üretimde mutlaka TLS arkasında çalıştırın.** Bearer token, `Authorization` header'ında olduğu gibi aktarılır; otomatik Let's Encrypt sertifikalı bir reverse proxy (Caddy, nginx, Traefik) bu iş için alışılmış yoldur.
- **`/health` uç noktası** her koşulda `200 OK` döner (kimlik doğrulamadan muaftır); böylece reverse-proxy probe'ları ve Docker `HEALTHCHECK` direktifleri token olmadan çalışır.
- **Bugün yalnızca tek kiracılıdır.** Tüm HTTP istemcileri tek bir `SeptumEngine`'i ve dolayısıyla tek bir anonimleştirme session registry'sini paylaşır. Çok kiracılı izolasyon (istemci başına ayrışmış session'lar) yol haritasındadır; şimdilik izolasyon önemliyse her kiracı için ayrı bir örnek çalıştırın.

## Ortam değişkenleri

**Çekirdek / tüm taşımalarda ortak:**

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `SEPTUM_REGULATIONS` | 17 paketin tamamı | Virgülle ayrılmış regülasyon paket id'leri (örneğin `gdpr,kvkk,hipaa`). |
| `SEPTUM_LANGUAGE` | `en` | Araç çağrısı dil belirtmediğinde kullanılacak ISO 639-1 varsayılanı. |
| `SEPTUM_USE_NER` | `true` | Transformer NER katmanını etkinleştirir. Model indirmesinden kaçınmak için `false` yapın. |
| `SEPTUM_USE_PRESIDIO` | `true` | Presidio tanıyıcı katmanını etkinleştirir. |
| `SEPTUM_SESSION_TTL` | `3600` | Boşta kalan bir anonimleştirme session'ının atılacağı saniye sayısı. `0` eviction'ı tamamen kapatır. |

**Yalnız HTTP taşımasında (stdio modunda yok sayılır):**

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `SEPTUM_MCP_TRANSPORT` | `stdio` | Bağlanılacak taşıma: `stdio`, `streamable-http` ya da `sse`. |
| `SEPTUM_MCP_HTTP_HOST` | `127.0.0.1` | Dinleme adresi. `0.0.0.0`'a yalnız TLS + kimlik doğrulama arkasında bağlayın. |
| `SEPTUM_MCP_HTTP_PORT` | `8765` | TCP portu. |
| `SEPTUM_MCP_HTTP_TOKEN` | ayarlı değil | `Authorization: Bearer <token>` için bearer token. Ayarlı değilse HTTP kimlik doğrulama olmadan çalışır (yalnız localhost). |
| `SEPTUM_MCP_HTTP_MOUNT_PATH` | SDK varsayılanı | URL yol öneki (streamable-http için `/mcp`, sse için `/sse`). |

Desteklenen regülasyon paket id'leri: `gdpr`, `kvkk`, `ccpa`, `cpra`, `hipaa`, `pipeda`, `lgpd`, `pdpa_th`, `pdpa_sg`, `appi`, `pipl`, `popia`, `dpdp`, `uk_gdpr`, `pdpl_sa`, `nzpa`, `australia_pa`.

## Kullanım örnekleri

### Uzak modele göndermeden önce maskele

```jsonc
// Araç çağrısı
{
  "name": "mask_text",
  "arguments": {
    "text": "1234 numaralı fatura hakkında Ayşe Yılmaz'a ayse@ornek.com adresinden ulaşın.",
    "language": "tr"
  }
}

// Cevap
{
  "masked_text": "1234 numaralı fatura hakkında [PERSON_1]'a [EMAIL_ADDRESS_1] adresinden ulaşın.",
  "session_id": "e8f1...",
  "entity_count": 2,
  "entity_type_counts": { "PERSON": 1, "EMAIL_ADDRESS": 1 }
}
```

### LLM cevabını geri yaz

```jsonc
{
  "name": "unmask_response",
  "arguments": {
    "text": "[PERSON_1]'a [EMAIL_ADDRESS_1] adresinden bir takip mesajı hazırladım.",
    "session_id": "e8f1..."
  }
}
```

### Yerel bir dosyayı tara

```jsonc
{
  "name": "scan_file",
  "arguments": {
    "file_path": "/Users/ayse/notlar/musteri.pdf",
    "mask": true
  }
}
```

## Desteklenen dosya formatları

`scan_file` aracı düz metin, Markdown, CSV, JSON, PDF (`pypdf` ile) ve DOCX (`python-docx` ile) formatlarını işler. OCR (taranmış PDF, görseller) ve ses transkripsiyonu bu paketin kapsamı dışında bırakılmıştır; yüzlerce megabaytlık modeller gerektirir ve yeri Septum API/ingest hattıdır.

## Kapsam ve güvenlik notları

- **septum-mcp air-gapped'tir.** Dışarıya çıkan hiçbir ağ çağrısı yoktur. Opsiyonel NER katmanı bile modelleri yerel Hugging Face cache'inden yükler. `SEPTUM_USE_NER`'ı kapatırsanız hiç model indirmesi olmaz.
- **Session'lar yalnızca bellektedir.** Anonimleştirme haritaları sunucu süreci içinde yaşar; sunucu sonlandığında ya da session başına TTL dolduğunda düşer. Diske hiçbir şey yazılmaz.
- **`get_session_map` ham PII döner.** Yalnız yerel debug araçları için tasarlanmıştır. Çıktısını uzak bir sisteme aktarmayın.
- **HTTP modu, loopback olmayan her bağlanmada bearer token ister.** `SEPTUM_MCP_HTTP_TOKEN`, `/health` dışındaki her isteğin önünde durur; tokensiz ve loopback olmayan bir host sesli bir başlangıç uyarısı basar. Sunucunun önünde mutlaka TLS sonlandırın (reverse proxy) ki token düz metinle gitmesin.
- **HTTP modu bugün tek kiracılıdır.** Bağlı tüm istemciler tek bir `SeptumEngine`'i ve tek bir session registry'sini paylaşır. İzolasyon önemliyse her kiracı için ayrı bir örnek çalıştırın.

## Test paketini çalıştırma

```bash
pip install -e 'packages/core[transformers]'
pip install -e 'packages/mcp[test]'
pytest packages/mcp/tests/
```

Paket config parse'ını, dosya okuyucularını, altı aracın tamamını ve gerçek sunucuyu alt süreç olarak başlatan bir uçtan uca stdio smoke testini kapsar.
