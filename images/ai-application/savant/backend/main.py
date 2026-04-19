from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import os
import json
import uuid
from typing import AsyncGenerator
from fastapi.responses import StreamingResponse

# OpenTelemetry Imports
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

# ── OTEL Setup ────────────────────────────────────────────────────────────────

OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://alloy.monitoring.svc:4317")

# Using strings for keys to avoid dependency version mismatches with Semantic Conventions
resource = Resource(attributes={
    "service.name": "savant",
    "deployment.environment": "homelab"
})

provider = TracerProvider(resource=resource)
processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True))
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

app = FastAPI(title="Savant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Instrument the app
FastAPIInstrumentor.instrument_app(app)
HTTPXClientInstrumentor().instrument()

OLLAMA_URL   = os.getenv("OLLAMA_URL",   "http://vllm-reasoning.ai-platform.svc.cluster.local:11434")
QDRANT_URL   = os.getenv("QDRANT_URL",   "http://qdrant.ai-platform.svc.cluster.local:6333")
EMBED_URL    = os.getenv("EMBED_URL",    "http://infinity-embedding.ai-platform.svc.cluster.local:8000")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "deepseek-r1:7b")
SEARCH_API   = os.getenv("SEARCH_API",   "https://api.duckduckgo.com/")
SEARXNG_URL  = os.getenv("SEARXNG_URL",  "http://searxng.ai-platform.svc.cluster.local:8080")
RERANK_URL   = os.getenv("RERANK_URL",   "http://infinity-embedding.ai-platform.svc.cluster.local:8001")
COLLECTION   = os.getenv("QDRANT_COLLECTION", "knowledge")


# ── Models ────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    stream: bool = True


# ── Helpers ───────────────────────────────────────────────────────────────────

async def get_embedding(text: str, request_id: str = None, session_id: str = None) -> list[float] | None:
    """Get embedding from vLLM embedding service."""
    with tracer.start_as_current_span("get_embedding") as span:
        span.set_attribute("gen_ai.request.model", "BAAI/bge-large-en-v1.5")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"{EMBED_URL}/v1/embeddings",
                    json={"model": "BAAI/bge-large-en-v1.5", "input": text},
                )
                r.raise_for_status()
                return r.json()["data"][0]["embedding"]
        except Exception as e:
            span.record_exception(e)
            return None

async def search_qdrant(embedding: list[float], top_k: int = 5) -> list[str]:
    """Search Qdrant for relevant context."""
    with tracer.start_as_current_span("search_qdrant") as span:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.post(
                    f"{QDRANT_URL}/collections/{COLLECTION}/points/search",
                    json={"vector": embedding, "limit": top_k, "with_payload": True},
                )
                r.raise_for_status()
                hits = r.json().get("result", [])
                return [h["payload"].get("text", "") for h in hits if h.get("score", 0) > 0.6]
        except Exception as e:
            span.record_exception(e)
            return []


async def rerank_documents(query: str, docs: list[str], top_k: int = 3, request_id: str = None, session_id: str = None) -> list[str]:
    """Rerank documents using BAAI/bge-reranker-large."""
    if not docs:
        return []
    with tracer.start_as_current_span("rerank_documents") as span:
        span.set_attribute("gen_ai.request.model", "BAAI/bge-reranker-large")
        span.set_attribute("gen_ai.usage.input_documents", len(docs))
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
                filtered_docs = [docs[res["index"]] for res in results if res.get("relevance_score", 0) > 0.2]
                return filtered_docs
        except Exception as e:
            span.record_exception(e)
            return docs[:top_k]

async def web_search(query: str, request_id: str = None, session_id: str = None) -> str:
    """SearxNG web search followed by reranking."""
    with tracer.start_as_current_span("web_search") as span:
        endpoints = [SEARXNG_URL, f"{SEARXNG_URL}/search"]
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json"
        }
        
        for url in endpoints:
            try:
                async with httpx.AsyncClient(timeout=15, headers=headers) as client:
                    r = await client.get(url, params={"q": query, "format": "json"})
                    if r.status_code != 200:
                        continue
                    
                    data = r.json()
                    results = data.get("results", [])
                    
                    if not results:
                        continue

                    docs: list[str] = []
                    for res in results[:20]:
                        content = res.get("content") or res.get("title", "")
                        if len(content) > 10:
                            docs.append(content)
                    
                    if not docs:
                        continue
                    
                    top_docs = await rerank_documents(query, docs, top_k=5, request_id=request_id, session_id=session_id)
                    return "\n\n".join(top_docs) if top_docs else ""
            except Exception as e:
                span.record_exception(e)
                continue
        return ""


def build_messages(user_msg: str, context: str, source: str) -> list[dict]:
    """Build a list of messages for Ollama /api/chat."""
    if source == "qdrant":
        system = (
            "You are a helpful assistant with access to an internal knowledge base. "
            "Use the provided context to answer the user's question accurately and concisely. "
            "For comparisons or multi-entity analysis, always provide the output in a clean Markdown table. "
            "If the context doesn't fully answer the question, say so."
        )
        ctx_msg = f"Internal Knowledge:\n{context}"
    elif source == "web":
        system = (
            "You are a helpful assistant. The following information was retrieved from the internet. "
            "Use it to answer the question concisely. For logical comparisons, prefer using Markdown tables."
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
    stats_out: dict
) -> AsyncGenerator[str, None]:
    """Stream response from Ollama /api/chat and yield SSE chunks."""
    with tracer.start_as_current_span("ollama_chat") as span:
        span.set_attribute("gen_ai.request.model", model)
        
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
                            
                            span.set_attribute("gen_ai.usage.prompt_tokens", pt)
                            span.set_attribute("gen_ai.usage.completion_tokens", ct)
                            
                            stats_out.update(
                                prompt_tokens=pt,
                                completion_tokens=ct,
                                duration_ms=dm,
                            )
                            yield f"data: {json.dumps({'done': True, 'prompt_tokens': pt, 'completion_tokens': ct, 'duration_ms': dm})}\n\n"
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

    request_id = str(uuid.uuid4())
    session_id = "default-session"
    
    with tracer.start_as_current_span("chat_request") as span:
        span.set_attribute("app.request_id", request_id)
        span.set_attribute("app.user_message", user_msg)

        embedding = await get_embedding(user_msg, request_id=request_id, session_id=session_id)
        
        local_hits: list[str] = []
        if embedding:
            local_hits = await search_qdrant(embedding)

        web_results = await web_search(user_msg, request_id=request_id, session_id=session_id)
        web_hits = web_results.split("\n\n") if web_results else []

        context = ""
        source  = "none"

        if web_hits:
            context = "\n\n---\n\n".join(web_hits)
            source  = "web"
            if local_hits:
                context += "\n\n--- [Internal Background] ---\n\n" + "\n\n---\n\n".join(local_hits[:2])
        elif local_hits:
            context = "\n\n---\n\n".join(local_hits)
            source  = "qdrant"

        span.set_attribute("app.context_source", source)

        messages = build_messages(user_msg, context, source)
        stats: dict = {}

        async def event_stream():
            meta = {"source": source, "has_context": bool(context)}
            yield f"data: {json.dumps({'meta': meta})}\n\n"
            async for chunk in stream_ollama(messages, OLLAMA_MODEL, stats):
                yield chunk

        return StreamingResponse(event_stream(), media_type="text/event-stream")
