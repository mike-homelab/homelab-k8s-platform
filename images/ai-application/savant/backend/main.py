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
    from datetime import datetime
    start_time = datetime.now()
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
    finally:
        # Push telemetry after embedding completes
        if 'start_time' in locals():
            duration = (datetime.now() - start_time).total_seconds() * 1000
            await push_telemetry(text, "embed", duration, "BAAI/bge-large-en-v1.5")

async def push_telemetry(message: str, source: str, duration_ms: float, model: str, input_tokens: int = 0, output_tokens: int = 0):
    """Fire-and-forget telemetry push to Watchtower."""
    print(f"[*] Pushing telemetry: {source} ({duration_ms:.1f}ms)")
    try:
        payload = {
            "message":       message[:300],
            "input_tokens":  input_tokens,
            "output_tokens": output_tokens,
            "duration_ms":   duration_ms,
            "model":         model,
            "source":        source,
        }
        async with httpx.AsyncClient(timeout=2) as client:
            await client.post(f"{WATCHTOWER_URL}/api/ingest/telemetry", json=payload)
    except Exception:
        pass


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
    from datetime import datetime
    start_time = datetime.now()
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
    finally:
        if 'start_time' in locals():
            duration = (datetime.now() - start_time).total_seconds() * 1000
            await push_telemetry(query, "rerank", duration, "BAAI/bge-reranker-large", input_tokens=len(docs))

async def web_search(query: str) -> str:
    """SearxNG web search followed by reranking."""
    # Try the base endpoint first, then /search
    endpoints = [SEARXNG_URL, f"{SEARXNG_URL}/search"]
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }
    
    for url in endpoints:
        print(f"[*] Attempting web search: {url}")
        try:
            async with httpx.AsyncClient(timeout=15, headers=headers) as client:
                r = await client.get(url, params={"q": query, "format": "json"})
                if r.status_code != 200:
                    print(f"[!] {url} returned {r.status_code}")
                    continue
                
                data = r.json()
                results = data.get("results", [])
                print(f"[*] {url} results: {len(results)}")
                
                if not results:
                    continue

                docs: list[str] = []
                for res in results[:20]:
                    content = res.get("content") or res.get("title", "")
                    if len(content) > 10:
                        docs.append(content)
                
                if not docs:
                    continue
                
                top_docs = await rerank_documents(query, docs, top_k=5)
                return "\n\n".join(top_docs) if top_docs else ""
        except Exception as e:
            print(f"[!] Error hitting {url}: {e}")
            continue
            
    print("[!] All web search endpoints failed or returned no results")
    return ""


def build_messages(user_msg: str, context: str, source: str) -> list[dict]:
    """Build a list of messages for Ollama /api/chat."""
    if source == "qdrant":
        system = (
            "You are a helpful assistant with access to an internal knowledge base. "
            "Use the provided context to answer the user's question accurately and concisely. "
            "If the context doesn't fully answer the question, say so."
        )
        ctx_msg = f"Internal Knowledge:\n{context}"
    elif source == "web":
        system = (
            "You are a helpful assistant. The following information was retrieved from the internet. "
            "Use it to answer the question concisely."
        )
        ctx_msg = f"Web Search Context:\n{context}"
    else:
        system = (
            "You are a helpful assistant. Provide the best answer based on your general knowledge."
        )
        ctx_msg = ""

    messages = [{"role": "system", "content": system}]
    if ctx_msg:
        messages.append({"role": "user", "content": f"Context for my next question:\n{ctx_msg}"})
        messages.append({"role": "assistant", "content": "Understood. I will use that context. What is your question?"})
    
    messages.append({"role": "user", "content": user_msg})
    return messages


async def stream_ollama(
    messages: list[dict],
    model: str,
    stats_out: dict,
) -> AsyncGenerator[str, None]:
    """Stream response from Ollama /api/chat and yield SSE chunks."""
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {"num_ctx": 8192, "stop": ["<|end|>", "User:", "Assistant:"]}
    }
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    message = chunk.get("message", {})
                    token = message.get("content", "")
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

    print(f"[*] Processing query: {user_msg}")

    # 1. Start both searches concurrently for speed (Hybrid RAG)
    embedding = await get_embedding(user_msg)
    
    local_hits: list[str] = []
    if embedding:
        local_hits = await search_qdrant(embedding)
        print(f"[*] Qdrant hits: {len(local_hits)}")

    # 2. Always check web fallback if local hits are low or as a primary for "latest" intent
    web_results = await web_search(user_msg)
    web_hits = web_results.split("\n\n") if web_results else []
    print(f"[*] Web search hits (after rerank): {len(web_hits) if web_hits else 0}")

    # 3. Consolidate and categorize
    context = ""
    source  = "none"

    if web_hits:
        # If we have web hits, they are likely more current
        context = "\n\n---\n\n".join(web_hits)
        source  = "web"
        if local_hits:
            context += "\n\n--- [Internal Background] ---\n\n" + "\n\n---\n\n".join(local_hits[:2])
    elif local_hits:
        context = "\n\n---\n\n".join(local_hits)
        source  = "qdrant"

    # 4. Build messages
    messages = build_messages(user_msg, context, source)

    # 5. Stream response, collect stats, then push to Watchtower
    stats: dict = {}

    async def event_stream():
        # First send metadata about the context source
        meta = {"source": source, "has_context": bool(context)}
        yield f"data: {json.dumps({'meta': meta})}\n\n"

        async for chunk in stream_ollama(messages, OLLAMA_MODEL, stats):
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
                pass 

    return StreamingResponse(event_stream(), media_type="text/event-stream")
