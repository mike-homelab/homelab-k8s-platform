# AI Application Wiki

This document outlines the architecture, components, and purpose of the applications running under the `ai-application` section of the Homelab Kubernetes Platform. These services provide higher-level AI capabilities and observability for the core AI platform infrastructure.

---

## 1. Savant (formerly Knowledge Chat)

**Savant** is the internal AI knowledge base assistant. It provides a conversational interface backed by a hybrid Retrieval-Augmented Generation (RAG) pipeline to accurately answer questions based on the homelab's context, falling back to real-time web search if needed.

- **Ingress domain**: `raphael.michaelhomelab.work`
- **Namespace**: `ai-platform`
- **Image**: `docker.michaelhomelab.work/homelab-docker-repo/ai-application/savant:latest`
- **CI workflow**: `.github/workflows/build-savant.yaml`
- **K8s manifests**: `clusters/homelab/apps/ai-application/savant/`

### Components

| Component | Technology | Details |
|---|---|---|
| Frontend | React / Vite | Chat UI with SSE streaming, source badges, per-message token stats |
| Backend | Python FastAPI | Async streaming, RAG pipeline, Watchtower telemetry push |
| Embedding | `infinity-embedding` | Model: `BAAI/bge-large-en-v1.5` |
| Vector DB | Qdrant | Collection: `knowledge` |
| Reasoning LLM | `vllm-reasoning` | Ollama-compat, model: `phi3.5` |
| Fallback Search | DuckDuckGo | Instant-answer API for queries with no Qdrant context |

### Request Flow

```
User query
  ↓
1. Embed query via infinity-embedding (BAAI/bge-large-en-v1.5)
  ↓
2. Semantic search in Qdrant (score > 0.6 threshold)
  ↓  (if no hits)
3. Fallback: DuckDuckGo web search
  ↓
4. Build context-aware prompt (system + context block + user message)
  ↓
5. Stream response from Ollama phi3.5
  ↓
6. On stream "done": fire-and-forget POST → Watchtower /api/ingest/savant
     payload: { message, input_tokens, output_tokens, duration_ms, model, source }
```

### Watchtower Telemetry Push

After every completed chat response, Savant's backend captures token counts and duration from Ollama's final `done` chunk and pushes them to Watchtower (fire-and-forget, 3s timeout, silent on failure):

```python
# env var: WATCHTOWER_URL (default: http://watchtower.ai-platform.svc.cluster.local)
POST /api/ingest/savant
{
  "message":       "<user input>",
  "input_tokens":  <prompt_eval_count from Ollama>,
  "output_tokens": <eval_count from Ollama>,
  "duration_ms":   <total_duration / 1e6>,
  "model":         "phi3.5",
  "source":        "qdrant" | "web" | "none"
}
```

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_URL` | `http://vllm-reasoning.ai-platform.svc.cluster.local:11434` | Ollama generation endpoint |
| `OLLAMA_MODEL` | `phi3.5` | Model name for generation |
| `QDRANT_URL` | `http://qdrant.ai-platform.svc.cluster.local:6333` | Qdrant vector database |
| `QDRANT_COLLECTION` | `knowledge` | Qdrant collection name |
| `EMBED_URL` | `http://infinity-embedding.ai-platform.svc.cluster.local:8000` | Embedding service endpoint |
| `WATCHTOWER_URL` | `http://watchtower.ai-platform.svc.cluster.local` | Watchtower ingest endpoint |

---

## 2. Watchtower (formerly Sentinel)

**Watchtower** is the LLM inference observability dashboard. It provides real-time visibility into the performance and usage of the AI platform's models. The **Reasoning tab** is powered by events pushed directly from Savant, stored durably in PostgreSQL, and served via a Redis cache.

- **Ingress domain**: `watchtower.michaelhomelab.work`
- **Namespace**: `ai-platform`
- **Image**: `docker.michaelhomelab.work/homelab-docker-repo/ai-application/watchtower:latest`
- **CI workflow**: `.github/workflows/build-watchtower.yaml`
- **K8s manifests**: `clusters/homelab/apps/ai-application/watchtower/`

### Components

| Component | Technology | Details |
|---|---|---|
| Frontend | React / Vite | Tabbed feed UI with 30s auto-refresh, search, token/latency cards |
| Backend | Python FastAPI | asyncpg + aioredis; lifespan manages pool lifecycle; DDL auto-run |
| Primary store | PostgreSQL 16 | `savant_inference` table, Longhorn 10 Gi PVC, `ai-platform` namespace |
| Cache | Redis 7 | 30s TTL cache-aside on `/api/feed/reasoning`; busted on each ingest |
| Log source | Loki | Supplementary Ollama JSON log parsing for Coder feed |
| Trace source | Tempo | OpenTelemetry traces for Embedding and Reranker feeds |

### Feed Sources

| Tab | Primary Source | Supplementary |
|---|---|---|
| **Reasoning** | PostgreSQL (`savant_inference` table, pushed by Savant) | Loki `vllm-reasoning` logs (best-effort) |
| **Coder** | Loki `vllm-coder` logs | — |
| **Embedding** | Tempo traces (`/v1/embeddings`) | — |
| **Reranker** | Tempo traces (`/v1/rerank`) | — |

### Reasoning Feed — Data Path

```
Savant POST /api/ingest/savant
  ↓
INSERT INTO savant_inference (model, input_tokens, output_tokens, duration_ms, prompt, source)
  ↓
SCAN + DELETE matching Redis keys (feed:reasoning:*)
  ↓
Frontend polls GET /api/feed/reasoning?hours=6&limit=50
  ↓
  ├─ Redis cache HIT  → return immediately (TTL 30s)
  └─ Redis cache MISS → SELECT from Postgres → try Loki → SET cache → return
```

### Key Metrics Tracked

| Metric | Source field | Description |
|---|---|---|
| `input_tokens` | `prompt_eval_count` | Prompt token count from Ollama |
| `output_tokens` | `eval_count` | Completion token count from Ollama |
| `duration_ms` | `total_duration / 1e6` | End-to-end generation latency |
| `model` | `OLLAMA_MODEL` env | Model used for inference |
| `source` | Savant context source | `qdrant` / `web` / `none` (RAG context path used) |

### PostgreSQL Schema

```sql
CREATE TABLE IF NOT EXISTS savant_inference (
    id            BIGSERIAL PRIMARY KEY,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    model         TEXT,
    input_tokens  INT  NOT NULL DEFAULT 0,
    output_tokens INT  NOT NULL DEFAULT 0,
    duration_ms   FLOAT,
    prompt        TEXT,       -- truncated to 300 chars
    source        TEXT        -- qdrant | web | none
);
CREATE INDEX IF NOT EXISTS idx_savant_created ON savant_inference (created_at DESC);
```

The schema is created automatically at startup via `lifespan` (`CREATE TABLE IF NOT EXISTS` DDL).

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `LOKI_URL` | `http://loki-gateway.monitoring.svc.cluster.local/loki/api/v1` | Loki query endpoint |
| `TEMPO_URL` | `http://tempo-query-frontend.monitoring.svc.cluster.local:3100` | Tempo search endpoint |
| `POSTGRES_DSN` | `postgresql://watchtower:watchtower@postgres.ai-platform.svc.cluster.local:5432/watchtower` | asyncpg connection string |
| `REDIS_URL` | `redis://redis.ai-platform.svc.cluster.local:6379/0` | Redis connection URL |

---

## 3. PostgreSQL (Watchtower Store)

Dedicated PostgreSQL instance for Watchtower's persistent inference event storage.

- **Namespace**: `ai-platform`
- **ArgoCD Application**: `clusters/homelab/apps_argocd/ai-platform/postgres.yaml`
- **K8s manifests**: `clusters/homelab/apps/ai-platform/postgres/`
- **Sync-wave**: `10` (before Redis at `12`, Qdrant at `15`)

### Kubernetes Resources

| Resource | Kind | Details |
|---|---|---|
| `postgres` | StatefulSet | `postgres:16-alpine`, 1 replica, readiness + liveness probes |
| `postgres-data` | PVC (Longhorn) | 10 Gi, `ReadWriteOnce`, `storageClassName: longhorn` |
| `postgres` | Service (ClusterIP) | Port 5432, internal to `ai-platform` namespace |

### Database Credentials

| Field | Value |
|---|---|
| Database | `watchtower` |
| User | `watchtower` |
| Password | `watchtower` |
| Host | `postgres.ai-platform.svc.cluster.local:5432` |

> **Note**: Credentials are set via env vars in the StatefulSet manifest. No external ingress — internal only.

---

## 4. Redis (Cache)

Shared Redis instance used by Watchtower for the reasoning feed cache.

- **Namespace**: `ai-platform`
- **ArgoCD Application**: `clusters/homelab/apps_argocd/ai-platform/redis.yaml`
- **K8s manifests**: `clusters/homelab/apps/ai-platform/redis/`
- **Sync-wave**: `12`

### Usage by Watchtower

- Cache key pattern: `feed:reasoning:h{hours}:l{limit}:s{search}`
- TTL: **30 seconds** (matches the frontend auto-refresh interval)
- On ingest: all `feed:reasoning:*` keys are busted via `SCAN + DELETE`
- Includes `redis-exporter` sidecar for Prometheus metrics

---

## 5. Deployment Topology & Sync Order

```
ArgoCD sync-wave 10  →  postgres   (Longhorn PVC provisioned, DB ready)
ArgoCD sync-wave 12  →  redis      (cache available)
ArgoCD sync-wave 15  →  qdrant     (vector store available)
                     →  watchtower (lifespan connects: DB pool + Redis ping + DDL)
                     →  savant     (starts routing traffic; pushes telemetry to watchtower)
```

---

## Recent Updates

| Date | Change |
|---|---|
| 2026-04-13 | **Savant → Watchtower telemetry wiring**: Savant backend now pushes `input_tokens`, `output_tokens`, `duration_ms`, and `source` to Watchtower after every completed chat stream. |
| 2026-04-13 | **PostgreSQL added**: New StatefulSet (`postgres:16-alpine`, 10 Gi Longhorn PVC) deployed under ArgoCD sync-wave 10. Watchtower uses it as the durable store for `savant_inference` events. |
| 2026-04-13 | **Redis cache-aside**: Watchtower's `/api/feed/reasoning` endpoint now caches results in Redis (30s TTL). Cache is busted on each ingest event. |
| 2026-04-13 | **Watchtower backend rewrite**: Replaced in-memory `deque` with `asyncpg` (Postgres) + `redis[asyncio]`. Added FastAPI `lifespan` for clean connection pool lifecycle. Added DDL auto-creation on startup. |
| 2026-04-13 | **Renaming completed**: `knowledge-chat` → `savant`, `sentinel` → `watchtower`. All manifests, workflows, and UI text updated. |
