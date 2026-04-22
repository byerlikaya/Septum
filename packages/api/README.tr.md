# septum-api

> 🇬🇧 [English version](README.md)

[Septum](https://github.com/byerlikaya/Septum) için air-gapped FastAPI REST katmanı. Bootstrap yapılandırmasını, SQLAlchemy modellerini, router'ları, servisleri, middleware'i ve FastAPI uygulama nesnesini barındırır; hepsi bir araya geldiğinde gizlilik öncelikli ara katmanın sunucu tarafı oluşur. PII tespiti ve maskeleme bu paketin işi değildir — tümü [`septum-core`](../core/)'a devredilir; `septum-api` ne kendi başına anonimleştirme yapar, ne de doğrudan internete çıkar. Buluta giden LLM trafiği kuyruğun ötesinde, [`septum-gateway`](../gateway/) içinde yaşar.

## Kurulum

```bash
pip install -e packages/core
pip install -e packages/queue
pip install -e "packages/api[auth,rate-limit,postgres,server,test]"
# Ağır ML / ingest / OCR / Whisper bağımlılıkları requirements.txt'te:
pip install -r packages/api/requirements.txt
```

## Paket yerleşimi

| Modül | Açıklama |
|---|---|
| `septum_api.bootstrap` | `config.json` okuyucu/yazıcı; şifreleme anahtarını ve JWT secret'ını otomatik üretir. |
| `septum_api.config` | Script'ler için senkron `get_settings()` yardımcısı. |
| `septum_api.database` | Tembel başlatılan async SQLAlchemy engine, SQLite WAL ayarları, seed varsayılanları. |
| `septum_api.main` | FastAPI app factory + lifespan + middleware bağlantıları + OpenAPI özelleştirmesi. |
| `septum_api.models` | ORM taban sınıfı ve `AppSettings`, `User`, `Document`, `ChatSession`, `RegulationRuleset`, `ApiKey`, `AuditEvent`, `EntityDetection`, `ErrorLog`. |
| `septum_api.routers` | 14 APIRouter modülü (`auth`, `api_keys`, `chat`, `chat_sessions`, `chunks`, `documents`, `regulations`, `settings`, `setup`, `users`, `approval`, `audit`, `error_logs`, `text_normalization`). |
| `septum_api.services` | Doküman hattı, sanitizer sarmalayıcı, `llm_router`, `vector_store`, `bm25_retriever`, `deanonymizer`, `prompts`, `approval_gate`, `gateway_client` (kuyruk üreticisi), `ingestion/`, `llm_providers/`. |
| `septum_api.middleware` | `auth.py` (JWT + API anahtarı çözümü) ve `rate_limit.py` (slowapi + route bazlı limitler). |
| `septum_api.utils` | Crypto (AES-256-GCM), `auth_dependency`, Prometheus metrikleri, yapılandırılmış loglama. |
| `septum_api.seeds` | Yerleşik regülasyon ruleset'lerinin seed verisi. |
| `alembic/`, `alembic.ini` | Postgres şema migration'ları. `packages/api/` dizini altından `alembic upgrade head` ile uygulanır. |
| `scripts/docker-entrypoint.sh` | Container giriş betiği: bootstrap config → alembic upgrade → uvicorn. |
| `requirements.txt` | Ağır ML / OCR / Whisper / ingest bağımlılıkları (torch, Presidio, spaCy, PaddleOCR, Whisper, FAISS, BM25, langchain, …). `pyproject.toml`'da tutulmaz; böylece `pip install septum-api` hafif kalır. |

## Programatik kullanım

```python
from septum_api import bootstrap
from septum_api.database import build_database_url, initialize_engine
from septum_api.main import app  # FastAPI örneği

config = bootstrap.get_config()
initialize_engine(build_database_url(config.database_url, config.db_path))
# uvicorn septum_api.main:app
```

## Bölge kuralları

- **Ağ kütüphanelerini asla import etmeyin** (`requests`, `httpx`, `urllib`) — `septum-api` air-gapped bölgede yaşar. Bulut LLM çağrıları `septum-gateway`'e kuyruk üzerinden gider.
- **`septum-gateway`'den import yapmayın** — bağımlılık oku yalnızca ters yönde, `septum-queue` üzerinden ilerler.
- PII'yi gören tek bağımlılık `septum-core`'dur; tespit, maskeleme ve geri yazma işlemlerinin tamamı buradan geçer.
