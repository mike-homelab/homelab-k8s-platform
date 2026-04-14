from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncpg
import redis.asyncio as aioredis
import httpx
import os
import re
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────────

LOKI_URL     = os.getenv("LOKI_URL",     "http://loki-gateway.monitoring.svc.cluster.local/loki/api/v1")
TEMPO_URL    = os.getenv("TEMPO_URL",    "http://tempo-query-frontend.monitoring.svc.cluster.local:3100")
POSTGRES_DSN = os.getenv("POSTGRES_DSN", "postgresql://watchtower:watchtower@postgres.ai-platform.svc.cluster.local:5432/watchtower")
REDIS_URL    = os.getenv("REDIS_URL",    "redis://redis.ai-platform.svc.cluster.local:6379/0")

CACHE_TTL = 30  # seconds — matches the frontend 30s auto-refresh interval

# ── Schema DDL ────────────────────────────────────────────────────────────────

DDL = """
CREATE TABLE IF NOT EXISTS savant_inference (
    id            BIGSERIAL PRIMARY KEY,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    model         TEXT,
    input_tokens  INT  NOT NULL DEFAULT 0,
    output_tokens INT  NOT NULL DEFAULT 0,
    duration_ms   FLOAT,
    prompt        TEXT,
    source        TEXT
);
CREATE INDEX IF NOT EXISTS idx_savant_created ON savant_inference (created_at DESC);
"""

# ── Application state (shared across requests via app.state) ──────────────────

class AppState:
    db: asyncpg.Pool
    cache: aioredis.Redis


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create DB pool + Redis client on startup, close cleanly on shutdown."""
    state = AppState()

    # Postgres connection pool (min 1, max 5 — single-pod low traffic)
    state.db = await asyncpg.create_pool(
        POSTGRES_DSN,
        min_size=1,
        max_size=5,
        command_timeout=10,
    )
    async with state.db.acquire() as conn:
        await conn.execute(DDL)
    print("DB ready — savant_inference table ensured")

    # Redis client
    state.cache = aioredis.from_url(REDIS_URL, decode_responses=True)
    await state.cache.ping()
    print("Redis ready")

    app.state.db    = state.db
    app.state.cache = state.cache

    yield  # ── app running ──

    await state.db.close()
    await state.cache.aclose()


app = FastAPI(title="watchtower API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Pydantic model for Savant push events ─────────────────────────────────────

class SavantEvent(BaseModel):
    message: str          # user's input message (stored as prompt)
    input_tokens: int
    output_tokens: int
    duration_ms: float
    model: str
    source: str = "none"  # qdrant | web | none


# ── Helpers ───────────────────────────────────────────────────────────────────

def now_ns() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1e9)

def hours_ago_ns(h: int = 6) -> int:
    return int((datetime.now(timezone.utc) - timedelta(hours=h)).timestamp() * 1e9)

def _cache_key(hours: int, search: str, limit: int) -> str:
    return f"feed:reasoning:h{hours}:l{limit}:s{search}"


# ── Postgres helpers ──────────────────────────────────────────────────────────

async def db_insert_event(db: asyncpg.Pool, event: SavantEvent):
    await db.execute(
        """
        INSERT INTO savant_inference
            (model, input_tokens, output_tokens, duration_ms, prompt, source)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        event.model,
        event.input_tokens,
        event.output_tokens,
        event.duration_ms,
        event.message[:300],
        event.source,
    )


async def db_query_reasoning(
    db: asyncpg.Pool,
    hours: int,
    limit: int,
    search: str,
) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    if search:
        rows = await db.fetch(
            """
            SELECT created_at, model, input_tokens, output_tokens, duration_ms, prompt, source
            FROM savant_inference
            WHERE created_at >= $1
              AND prompt ILIKE $2
            ORDER BY created_at DESC
            LIMIT $3
            """,
            cutoff,
            f"%{search}%",
            limit,
        )
    else:
        rows = await db.fetch(
            """
            SELECT created_at, model, input_tokens, output_tokens, duration_ms, prompt, source
            FROM savant_inference
            WHERE created_at >= $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            cutoff,
            limit,
        )
    return [
        {
            "timestamp":     r["created_at"].isoformat(),
            "model":         r["model"] or "unknown",
            "input_tokens":  r["input_tokens"],
            "output_tokens": r["output_tokens"],
            "duration_ms":   r["duration_ms"],
            "prompt":        r["prompt"] or "",
            "source":        r["source"] or "none",
        }
        for r in rows
    ]


async def db_query_telemetry(
    db: asyncpg.Pool,
    source: str,
    hours: int,
    limit: int,
) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = await db.fetch(
        """
        SELECT created_at, model, input_tokens, output_tokens, duration_ms, prompt, source
        FROM savant_inference
        WHERE created_at >= $1
          AND source = $2
        ORDER BY created_at DESC
        LIMIT $3
        """,
        cutoff,
        source,
        limit,
    )
    return [
        {
            "timestamp":     r["created_at"].isoformat(),
            "model":         r["model"] or "unknown",
            "input_tokens":  r["input_tokens"],
            "output_tokens": r["output_tokens"],
            "duration_ms":   r["duration_ms"],
            "prompt":        r["prompt"] or "",
            "source":        r["source"],
        }
        for r in rows
    ]


# ── OLLAMA log parser (Loki) — kept as a supplementary source ─────────────────

def parse_ollama_line(raw: str, ts_ns: int) -> Optional[dict]:
    """Parse Ollama logs. Handles both JSON and legacy GIN formats."""
    # 1. Try Structured JSON
    try:
        data = json.loads(raw)
        if data.get("msg") in ("inference done", "request", "response"):
            prompt_tokens     = data.get("prompt_eval_count", 0)
            completion_tokens = data.get("eval_count", 0)
            total_ns          = data.get("total_duration", 0)
            if prompt_tokens or completion_tokens or total_ns:
                return {
                    "timestamp":     datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).isoformat(),
                    "model":         data.get("model", "unknown"),
                    "input_tokens":  prompt_tokens,
                    "output_tokens": completion_tokens,
                    "duration_ms":   round(total_ns / 1e6, 1),
                    "prompt":        data.get("prompt", "")[:300],
                    "source":        "loki",
                }
    except Exception:
        pass

    # 2. Fallback: Parse GIN logs
    # [GIN] 2026/04/14 - 10:34:40 | 200 |  1.264130923s |  10.244.251.131 | POST     "/api/generate"
    if "[GIN]" in raw and "|" in raw:
        try:
            parts = raw.split("|")
            if len(parts) >= 4:
                status = parts[1].strip()
                if status != "200": return None
                
                lat_str = parts[2].strip()
                ms = 0.0
                if "ms" in lat_str:  ms = float(lat_str.replace("ms", ""))
                elif "µs" in lat_str: ms = float(lat_str.replace("µs", "")) / 1000
                elif "s" in lat_str:   ms = float(lat_str.replace("s", "")) * 1000
                
                path = parts[4].strip() if len(parts) > 4 else "unknown"
                return {
                    "timestamp":     datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).isoformat(),
                    "model":         "ollama",
                    "input_tokens":  0,
                    "output_tokens": 0,
                    "duration_ms":   round(ms, 1),
                    "prompt":        f"API: {path}",
                    "source":        "loki",
                }
        except Exception:
            pass
            
    return None


async def loki_query(logql: str, limit: int = 100, hours: int = 6) -> list[dict]:
    params = {
        "query":     logql,
        "limit":     limit,
        "start":     hours_ago_ns(hours),
        "end":       now_ns(),
        "direction": "backward",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{LOKI_URL}/query_range", params=params)
        r.raise_for_status()
    results = []
    for stream in r.json().get("data", {}).get("result", []):
        for ts_str, line in stream.get("values", []):
            parsed = parse_ollama_line(line, int(ts_str))
            if parsed:
                results.append(parsed)
    return results


# ── Tempo query helper ────────────────────────────────────────────────────────

async def tempo_search(service: str, limit: int = 50, hours: int = 6) -> list[dict]:
    start = datetime.now(timezone.utc) - timedelta(hours=hours)
    params = {
        "service.name": service,
        "limit":        limit,
        "start":        start.isoformat(),
        "end":          datetime.now(timezone.utc).isoformat(),
    }
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{TEMPO_URL}/api/search", params=params)
        r.raise_for_status()
    spans = []
    for trace in r.json().get("traces", []):
        root = trace.get("rootSpanName", "")
        if not any(x in root for x in ["/v1/embeddings", "/v1/rerank", "chat", "completion"]):
            continue
        attrs = trace.get("spanSets", [{}])[0].get("spans", [{}])[0].get("attributes", {})
        def ga(key, default=None):
            return attrs.get(key, {}).get("Value", {}).get("StringValue") or \
                   attrs.get(key, {}).get("Value", {}).get("IntValue") or default
        spans.append({
            "timestamp":     trace.get("startTimeUnixNano", ""),
            "trace_id":      trace.get("traceID", ""),
            "model":         ga("gen_ai.request.model", "unknown"),
            "input_tokens":  int(ga("gen_ai.usage.prompt_tokens",    0)),
            "output_tokens": int(ga("gen_ai.usage.completion_tokens", 0)),
            "duration_ms":   round(int(trace.get("durationMs", 0)), 1),
            "prompt":        ga("gen_ai.request.messages", "")[:300],
        })
    return spans


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/ingest/savant")
async def ingest_savant(event: SavantEvent):
    """Legacy ingest for Savant Chat events."""
    await db_insert_event(app.state.db, event)
    # Invalidate reasoning feeds
    await _invalidate_cache("feed:reasoning:*")
    return {"ok": True}


@app.post("/api/ingest/telemetry")
async def ingest_telemetry(event: SavantEvent):
    """Generic ingest for any AI platform telemetry (savant, rerank, embed, coder)."""
    await db_insert_event(app.state.db, event)
    
    # Invalidate relevant caches
    if event.source == "rerank":
        await _invalidate_cache("feed:reranker:*")
    elif event.source == "embed":
        await _invalidate_cache("feed:embedding:*")
    elif event.source == "coder":
        await _invalidate_cache("feed:coder:*")
    else:
        await _invalidate_cache("feed:reasoning:*")
        
    return {"ok": True}


async def _invalidate_cache(pattern: str):
    cursor = 0
    while True:
        cursor, keys = await app.state.cache.scan(cursor, match=pattern, count=100)
        if keys:
            await app.state.cache.delete(*keys)
        if cursor == 0:
            break


@app.get("/api/feed/reasoning")
async def feed_reasoning(search: str = "", limit: int = 50, hours: int = 6):
    """Reasoning feed — served from Redis cache (30s TTL), backed by Postgres.

    On cache miss:
      1. Query Postgres for Savant inference events (primary source)
      2. Try Loki for supplementary Ollama log events (best-effort)
      3. Merge, cap to limit, store in Redis, return
    """
    cache_key = _cache_key(hours, search, limit)

    # ── Cache HIT ──
    cached = await app.state.cache.get(cache_key)
    if cached:
        return {"items": json.loads(cached), "cache": "hit"}

    # ── Cache MISS — query Postgres ──
    pg_items: list[dict] = []
    try:
        pg_items = await db_query_reasoning(app.state.db, hours, limit, search)
    except Exception as exc:
        print(f"Postgres query error: {exc}")

    # ── Supplementary: Loki (best-effort) ──
    loki_items: list[dict] = []
    try:
        logql = '{namespace="ai-platform", app="vllm-reasoning"}'
        if search:
            logql = f'{logql} |~ `(?i){re.escape(search)}`'
        loki_items = await loki_query(logql, limit=limit * 3, hours=hours)
    except Exception:
        pass  # Loki unavailable — silently skip

    combined = pg_items + loki_items
    combined = combined[:limit]

    # ── Populate cache ──
    try:
        await app.state.cache.setex(cache_key, CACHE_TTL, json.dumps(combined))
    except Exception:
        pass  # Cache write failure is non-fatal

    return {"items": combined, "cache": "miss"}


@app.get("/api/feed/coder")
async def feed_coder(search: str = "", limit: int = 50, hours: int = 6):
    """Ollama coder feed — parsed from Loki logs."""
    logql = '{namespace="ai-platform", app="vllm-coder"}'
    if search:
        logql = f'{logql} |~ `(?i){re.escape(search)}`'
    items = await loki_query(logql, limit=limit * 3, hours=hours)
    if search:
        items = [i for i in items if search.lower() in (i.get("prompt") or "").lower()]
    return {"items": items[:limit]}


@app.get("/api/feed/embedding")
async def feed_embedding(search: str = "", limit: int = 50, hours: int = 6):
    """vLLM embedding feed — backed by Postgres."""
    items = await db_query_telemetry(app.state.db, "embed", hours, limit)
    if search:
        items = [i for i in items if search.lower() in (i.get("prompt") or "").lower()]
    return {"items": items}


@app.get("/api/feed/reranker")
async def feed_reranker(search: str = "", limit: int = 50, hours: int = 6):
    """vLLM reranker feed — backed by Postgres."""
    items = await db_query_telemetry(app.state.db, "rerank", hours, limit)
    if search:
        items = [i for i in items if search.lower() in (i.get("prompt") or "").lower()]
    return {"items": items}
