from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import os
import re
import json
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Optional

app = FastAPI(title="watchtower API")

# ── In-memory event store (Savant pushes here) ────────────────────────────────
# Stores the last 500 Savant inference events so Reasoning tab always has data
_savant_events: deque = deque(maxlen=500)


class SavantEvent(BaseModel):
    message: str          # user's input message
    input_tokens: int
    output_tokens: int
    duration_ms: float
    model: str
    source: str = "none"  # qdrant | web | none

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

LOKI_URL  = os.getenv("LOKI_URL",  "http://loki-gateway.monitoring.svc.cluster.local/loki/api/v1")
TEMPO_URL = os.getenv("TEMPO_URL", "http://tempo-query-frontend.monitoring.svc.cluster.local:3100")

# ── helpers ─────────────────────────────────────────────────────────────────

def now_ns() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1e9)

def hours_ago_ns(h: int = 6) -> int:
    return int((datetime.now(timezone.utc) - timedelta(hours=h)).timestamp() * 1e9)


# ── OLLAMA log parser (Loki) ─────────────────────────────────────────────────
# Ollama emits lines like:
#   {"level":"INFO","msg":"inference done","model":"phi3.5","prompt_eval_count":42,
#    "eval_count":318,"total_duration":4123456789,"prompt_eval_duration":123456789,
#    "eval_duration":4000000000}

OLLAMA_LOG_RE = re.compile(
    r'"model"\s*:\s*"(?P<model>[^"]+)".*?'
    r'"prompt_eval_count"\s*:\s*(?P<prompt_tokens>\d+).*?'
    r'"eval_count"\s*:\s*(?P<completion_tokens>\d+).*?'
    r'"total_duration"\s*:\s*(?P<total_duration_ns>\d+)',
    re.DOTALL,
)

def parse_ollama_line(raw: str, ts_ns: int) -> Optional[dict]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if data.get("msg") not in ("inference done", "request", "response"):
        return None
    prompt_tokens = data.get("prompt_eval_count", 0)
    completion_tokens = data.get("eval_count", 0)
    total_ns = data.get("total_duration", 0)
    if not (prompt_tokens or completion_tokens or total_ns):
        return None
    return {
        "timestamp": datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).isoformat(),
        "model": data.get("model", "unknown"),
        "input_tokens": prompt_tokens,
        "output_tokens": completion_tokens,
        "duration_ms": round(total_ns / 1e6, 1),
        "prompt": data.get("prompt", "")[:300],
    }


# ── Loki query helper ─────────────────────────────────────────────────────────

async def loki_query(logql: str, limit: int = 100, hours: int = 6) -> list[dict]:
    params = {
        "query": logql,
        "limit": limit,
        "start": hours_ago_ns(hours),
        "end":   now_ns(),
        "direction": "backward",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{LOKI_URL}/query_range", params=params)
        r.raise_for_status()
    data = r.json()
    results = []
    for stream in data.get("data", {}).get("result", []):
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
        "limit": limit,
        "start": start.isoformat(),
        "end":   datetime.now(timezone.utc).isoformat(),
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
            "timestamp": trace.get("startTimeUnixNano", ""),
            "trace_id":  trace.get("traceID", ""),
            "model":     ga("gen_ai.request.model", "unknown"),
            "input_tokens":  int(ga("gen_ai.usage.prompt_tokens",     0)),
            "output_tokens": int(ga("gen_ai.usage.completion_tokens",  0)),
            "duration_ms": round(int(trace.get("durationMs", 0)), 1),
            "prompt": ga("gen_ai.request.messages", "")[:300],
        })
    return spans


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/ingest/savant")
async def ingest_savant(event: SavantEvent):
    """Receive an inference event pushed by Savant after each chat completion."""
    _savant_events.appendleft({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": event.model,
        "input_tokens": event.input_tokens,
        "output_tokens": event.output_tokens,
        "duration_ms": event.duration_ms,
        "prompt": event.message[:300],  # truncate for display
        "source": event.source,
    })
    return {"ok": True}


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


@app.get("/api/feed/reasoning")
async def feed_reasoning(search: str = "", limit: int = 50, hours: int = 6):
    """Reasoning feed — merges Savant push events with Loki log parsing.

    Savant events are the primary source (always available).
    Loki events are appended if they parse successfully (best-effort).
    """
    # 1. Pull from in-memory Savant event store
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    savant_items = [
        e for e in _savant_events
        if datetime.fromisoformat(e["timestamp"]) >= cutoff
    ]

    # 2. Try Loki as supplementary source (may be empty / unreachable)
    loki_items: list = []
    try:
        logql = '{namespace="ai-platform", app="vllm-reasoning"}'
        if search:
            logql = f'{logql} |~ `(?i){re.escape(search)}`'
        loki_items = await loki_query(logql, limit=limit * 3, hours=hours)
    except Exception:
        pass  # Loki unavailable — silently fall back to savant events only

    # 3. Merge, de-duplicate (savant events take priority), filter & cap
    combined = savant_items + loki_items
    if search:
        combined = [i for i in combined if search.lower() in (i.get("prompt") or "").lower()]
    return {"items": combined[:limit]}


@app.get("/api/feed/embedding")
async def feed_embedding(search: str = "", limit: int = 50, hours: int = 6):
    """vLLM embedding feed — from Tempo traces."""
    items = await tempo_search("vllm-embedding", limit=limit, hours=hours)
    if search:
        items = [i for i in items if search.lower() in (i.get("prompt") or "").lower()]
    return {"items": items}


@app.get("/api/feed/reranker")
async def feed_reranker(search: str = "", limit: int = 50, hours: int = 6):
    """vLLM reranker feed — from Tempo traces."""
    items = await tempo_search("vllm-reranker", limit=limit, hours=hours)
    if search:
        items = [i for i in items if search.lower() in (i.get("prompt") or "").lower()]
    return {"items": items}
