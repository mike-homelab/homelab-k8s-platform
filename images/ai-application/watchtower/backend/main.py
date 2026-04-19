from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
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
REDIS_URL    = os.getenv("REDIS_URL",    "redis://redis.ai-platform.svc.cluster.local:6379/0")

CACHE_TTL = 30  # seconds

# ── Application state ─────────────────────────────────────────────────────────

class AppState:
    cache: aioredis.Redis


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create Redis client on startup, close cleanly on shutdown."""
    state = AppState()

    # Redis client
    state.cache = aioredis.from_url(REDIS_URL, decode_responses=True)
    await state.cache.ping()
    print("Redis ready")

    app.state.cache = state.cache

    yield  # ── app running ──

    await state.cache.aclose()


app = FastAPI(title="watchtower API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Pydantic model (kept for legacy reasons or empty) ─────────────────────────

class SavantEvent(BaseModel):
    message: str
    input_tokens: int
    output_tokens: int
    duration_ms: float
    model: str
    source: str = "none"
    request_id: Optional[str] = None
    session_id: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def now_ns() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1e9)

def hours_ago_ns(h: int = 6) -> int:
    return int((datetime.now(timezone.utc) - timedelta(hours=h)).timestamp() * 1e9)

def _cache_key(hours: int, search: str, limit: int) -> str:
    return f"feed:reasoning:v2:h{hours}:l{limit}:s{search}"


# ── OLLAMA log parser (Loki) ──────────────────────────────────────────────────

def parse_ollama_line(raw: str, ts_ns: int) -> Optional[dict]:
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


# ── Tempo query (Primary Data Source now) ─────────────────────────────────────

async def tempo_search(service: str, limit: int = 50, hours: int = 6) -> list[dict]:
    """Search Tempo for Savant traces."""
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
        # We look for the main chat_request span or nested ollama spans
        if not any(x in root for x in ["chat_request", "ollama_chat"]):
            continue
            
        # Extract attributes from the root span or first meaningful span
        span_sets = trace.get("spanSets", [])
        if not span_sets: continue
        
        main_span = span_sets[0].get("spans", [{}])[0]
        attrs = main_span.get("attributes", {})
        
        def ga(key, default=None):
            for a in attrs:
                if a.get("key") == key:
                    val = a.get("value", {})
                    return val.get("stringValue") or val.get("intValue") or default
            return default

        spans.append({
            "timestamp":     datetime.fromtimestamp(int(trace.get("startTimeUnixNano", 0)) / 1e9, tz=timezone.utc).isoformat(),
            "trace_id":      trace.get("traceID", ""),
            "model":         ga("gen_ai.request.model", "unknown"),
            "input_tokens":  int(ga("gen_ai.usage.prompt_tokens",    0)),
            "output_tokens": int(ga("gen_ai.usage.completion_tokens", 0)),
            "duration_ms":   round(int(trace.get("durationMs", 0)), 1),
            "prompt":        ga("app.user_message", "")[:300],
            "source":        ga("app.context_source", "none"),
        })
    return spans


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/feed/reasoning")
async def feed_reasoning(search: str = "", limit: int = 50, hours: int = 6):
    """Reasoning feed — Primary source is now TEMPO (OTel Traces)."""
    cache_key = _cache_key(hours, search, limit)

    cached = await app.state.cache.get(cache_key)
    if cached:
        return {"items": json.loads(cached), "cache": "hit"}

    # 1. Query Tempo for Savant traces
    tempo_items: list[dict] = []
    try:
        tempo_items = await tempo_search("savant", limit=limit, hours=hours)
    except Exception as exc:
        print(f"Tempo query error: {exc}")

    # 2. Query Loki for fallback (In-cluster local logs)
    loki_items: list[dict] = []
    try:
        logql = '{namespace="ai-platform", app="vllm-reasoning"}'
        if search:
            logql = f'{logql} |~ `(?i){re.escape(search)}`'
        loki_items = await loki_query(logql, limit=limit, hours=hours)
    except Exception:
        pass

    # Merge and sort (dedup by timestamp/prompt if needed, but simple merge for now)
    combined = tempo_items + loki_items
    combined.sort(key=lambda x: x["timestamp"], reverse=True)
    combined = combined[:limit]

    await app.state.cache.setex(cache_key, CACHE_TTL, json.dumps(combined))
    return {"items": combined, "cache": "miss"}


@app.post("/api/ingest/telemetry")
async def ingest_telemetry(event: SavantEvent):
    """Legacy endpoint - now a no-op as we use OTel."""
    return {"ok": True, "message": "Telemetry is now handled via OpenTelemetry/Alloy"}
