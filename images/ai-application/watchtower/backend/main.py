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
    state.cache = aioredis.from_url(REDIS_URL, decode_responses=True)
    await state.cache.ping()
    app.state.cache = state.cache
    yield
    await state.cache.aclose()


app = FastAPI(title="watchtower API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def now_ns() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1e9)

def hours_ago_ns(h: int = 6) -> int:
    return int((datetime.now(timezone.utc) - timedelta(hours=h)).timestamp() * 1e9)

def _cache_key(hours: int, search: str, limit: int, prefix: str = "feed") -> str:
    return f"{prefix}:v2:h{hours}:l{limit}:s{search}"


# ── Tempo query (Primary Data Source) ─────────────────────────────────────────

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
        if not any(x in root for x in ["chat_request", "ollama_chat"]):
            continue
            
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
@app.get("/api/feed/unified")  # Restoration of unified route for frontend compat
async def feed_unified(search: str = "", limit: int = 50, hours: int = 6):
    """Unified chronological feed grouping related operations by trace_id."""
    cache_key = _cache_key(hours, search, limit, "feed_unified")

    cached = await app.state.cache.get(cache_key)
    if cached:
        return {"items": json.loads(cached), "cache": "hit"}

    # 1. Fetch traces from Tempo
    items: list[dict] = []
    try:
        items = await tempo_search("savant", limit=limit, hours=hours)
    except Exception as exc:
        print(f"Tempo query error: {exc}")

    # 2. Sort and Cache
    items.sort(key=lambda x: x["timestamp"], reverse=True)
    
    # 3. For the 'unified' view, we can wrap each item in a 'steps' array to match frontend expectations
    # if the frontend expects a grouped structure.
    unified_items = []
    for it in items:
        unified_items.append({
            "timestamp": it["timestamp"],
            "request_id": it["trace_id"], # Map trace ID to request ID
            "prompt": it["prompt"],
            "steps": [{
                "source": it["source"],
                "model": it["model"],
                "input_tokens": it["input_tokens"],
                "output_tokens": it["output_tokens"],
                "duration_ms": it["duration_ms"]
            }]
        })

    await app.state.cache.setex(cache_key, CACHE_TTL, json.dumps(unified_items))
    return {"items": unified_items}


@app.post("/api/ingest/telemetry")
async def ingest_telemetry():
    return {"ok": True}
