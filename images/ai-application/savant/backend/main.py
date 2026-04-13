from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import os
import json
from typing import AsyncGenerator
from fastapi.responses import StreamingResponse

app = FastAPI(title="Savant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

OLLAMA_URL   = os.getenv("OLLAMA_URL",   "http://vllm-reasoning.ai-platform.svc.cluster.local:11434")
QDRANT_URL   = os.getenv("QDRANT_URL",   "http://qdrant.ai-platform.svc.cluster.local:6333")
EMBED_URL    = os.getenv("EMBED_URL",    "http://infinity-embedding.ai-platform.svc.cluster.local:8000")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "phi3.5")
SEARCH_API   = os.getenv("SEARCH_API",   "https://api.duckduckgo.com/")
SEARXNG_URL  = os.getenv("SEARXNG_URL",  "http://searxng.ai-platform.svc.cluster.local:8080")
RERANK_URL   = os.getenv("RERANK_URL",   "http://infinity-embedding.ai-platform.svc.cluster.local:8001")
COLLECTION   = os.getenv("QDRANT_COLLECTION", "knowledge")
WATCHTOWER_URL = os.getenv("WATCHTOWER_URL", "http://watchtower.ai-platform.svc.cluster.local")


# ── Models ────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    stream: bool = True


# ── Helpers ───────────────────────────────────────────────────────────────────

async def get_embedding(text: str) -> list[float] | None:
    """Get embedding from vLLM embedding service."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{EMBED_URL}/v1/embeddings",
                json={"model": "BAAI/bge-large-en-v1.5", "input": text},
            )
            r.raise_for_status()
            return r.json()["data"][0]["embedding"]
    except Exception:
        return None


async def search_qdrant(embedding: list[float], top_k: int = 5) -> list[str]:
    """Search Qdrant for relevant context."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.post(
                f"{QDRANT_URL}/collections/{COLLECTION}/points/search",
                json={"vector": embedding, "limit": top_k, "with_payload": True},
            )
            r.raise_for_status()
            hits = r.json().get("result", [])
            return [h["payload"].get("text", "") for h in hits if h.get("score", 0) > 0.6]
    except Exception:
        return []


async def rerank_documents(query: str, docs: list[str], top_k: int = 3) -> list[str]:
    """Rerank documents using BAAI/bge-reranker-large."""
    if not docs:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{RERANK_URL}/v1/rerank",
                json={
                    "model": "BAAI/bge-reranker-large",
                    "query": query,
                    "documents": docs,
                    "top_n": top_k
                }
            )
            r.raise_for_status()
            results = r.json().get("results", [])
            # Filter results by relevance_score > 0.2
            filtered_docs = [docs[res["index"]] for res in results if res.get("relevance_score", 0) > 0.2]
            return filtered_docs
    except Exception:
        # Fallback to returning top_k without filtering if reranker fails
        return docs[:top_k]

async def web_search(query: str) -> str:
    """SearxNG web search followed by reranking."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(SEARXNG_URL, params={"q": query, "format": "json"})
            r.raise_for_status()
            data = r.json()
            results = data.get("results", [])
            
            docs: list[str] = []
            for res in results[:10]:
                content = res.get("content") or res.get("title", "")
                if content:
                    docs.append(content)
            
            if not docs:
                return ""
            
            # Pipe results to the inference reranker
            top_docs = await rerank_documents(query, docs)
            return "\n\n".join(top_docs) if top_docs else ""
    except Exception:
        return ""


def build_prompt(user_msg: str, context: str, source: str) -> str:
    if source == "qdrant":
        system = (
            "You are a helpful assistant with access to an internal knowledge base. "
            "Use the provided context to answer the user's question accurately and concisely. "
            "If the context doesn't fully answer the question, say so."
        )
        ctx_block = f"[Internal Knowledge]\n{context}"
    elif source == "web":
        system = (
            "You are a helpful assistant. The internal knowledge base had no relevant information, "
            "so the following context was retrieved from the internet. Use it to answer the question."
        )
        ctx_block = f"[Web Search Results]\n{context}"
    else:
        system = (
            "You are a helpful assistant. Neither the internal knowledge base nor web search "
            "returned relevant results. Answer based on your general knowledge, and be transparent about it."
        )
        ctx_block = ""

    parts = [f"System: {system}"]
    if ctx_block:
        parts.append(ctx_block)
    parts.append(f"User: {user_msg}")
    return "\n\n".join(parts)


async def stream_ollama(
    prompt: str,
    model: str,
    stats_out: dict,
) -> AsyncGenerator[str, None]:
    """Stream response from Ollama and yield SSE chunks.

    Populates *stats_out* with prompt_tokens / completion_tokens / duration_ms
    once Ollama signals the stream is done.
    """
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": {"num_ctx": 8192},
    }
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream("POST", f"{OLLAMA_URL}/api/generate", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    token = chunk.get("response", "")
                    if token:
                        yield f"data: {json.dumps({'token': token})}\n\n"
                    if chunk.get("done"):
                        pt = chunk.get("prompt_eval_count", 0)
                        ct = chunk.get("eval_count", 0)
                        dm = round(chunk.get("total_duration", 0) / 1e6, 1)
                        stats_out.update(
                            prompt_tokens=pt,
                            completion_tokens=ct,
                            duration_ms=dm,
                        )
                        sse_stats = {
                            "done": True,
                            "prompt_tokens":     pt,
                            "completion_tokens": ct,
                            "duration_ms":       dm,
                        }
                        yield f"data: {json.dumps(sse_stats)}\n\n"
                except json.JSONDecodeError:
                    pass


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    user_msg = req.message.strip()
    if not user_msg:
        raise HTTPException(400, "Empty message")

    # 1. Embed the user message
    embedding = await get_embedding(user_msg)

    context = ""
    source  = "none"

    if embedding:
        # 2. Search Qdrant
        hits = await search_qdrant(embedding)
        if hits:
            context = "\n\n---\n\n".join(hits)
            source  = "qdrant"

    if not context:
        # 3. Fallback: web search
        web_ctx = await web_search(user_msg)
        if web_ctx:
            context = web_ctx
            source  = "web"

    # 4. Build prompt
    prompt = build_prompt(user_msg, context, source)

    # 5. Stream response, collect stats, then push to Watchtower
    stats: dict = {}

    async def event_stream():
        # First send metadata about the context source
        meta = {"source": source, "has_context": bool(context)}
        yield f"data: {json.dumps({'meta': meta})}\n\n"

        async for chunk in stream_ollama(prompt, OLLAMA_MODEL, stats):
            yield chunk

        # After the stream closes, fire-and-forget to Watchtower
        if stats:
            try:
                payload = {
                    "message":       user_msg,
                    "input_tokens":  stats.get("prompt_tokens", 0),
                    "output_tokens": stats.get("completion_tokens", 0),
                    "duration_ms":   stats.get("duration_ms", 0.0),
                    "model":         OLLAMA_MODEL,
                    "source":        source,
                }
                async with httpx.AsyncClient(timeout=3) as client:
                    await client.post(
                        f"{WATCHTOWER_URL}/api/ingest/savant",
                        json=payload,
                    )
            except Exception:
                pass  # Never block the user response due to telemetry failure

    return StreamingResponse(event_stream(), media_type="text/event-stream")
