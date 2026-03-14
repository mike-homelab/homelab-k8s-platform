import json
import os
import re
import uuid
from datetime import datetime, timezone

import httpx
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client.rest import ApiException
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from pydantic import BaseModel, Field
from prometheus_fastapi_instrumentator import Instrumentator

app = FastAPI(title="agent-api", version="0.11.0")

cors_allow_origins = [x.strip() for x in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allow_origins if cors_allow_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_k8s_ready = False
_otel_ready = False


def _ensure_k8s() -> None:
    global _k8s_ready
    if _k8s_ready:
        return
    try:
        k8s_config.load_incluster_config()
    except Exception:
        k8s_config.load_kube_config()
    _k8s_ready = True


def _ensure_otel() -> None:
    global _otel_ready
    if _otel_ready:
        return
    if os.getenv("OTEL_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        _otel_ready = True
        return

    endpoint = os.getenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "http://otel-collector.monitoring.svc.cluster.local:4318/v1/traces",
    )
    service_name = os.getenv("OTEL_SERVICE_NAME", "agent-api")

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()
    _otel_ready = True


_ensure_otel()
Instrumentator().instrument(app).expose(app, include_in_schema=False, endpoint="/metrics")


class ChatRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    model: str = Field(default="general", pattern="^(general|coder)$")
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=256, ge=1, le=2048)
    session_id: str | None = None


class ChatResponse(BaseModel):
    model: str
    text: str


class RagIndexRequest(BaseModel):
    text: str = Field(..., min_length=1)
    doc_id: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    collection: str = "rag-docs"
    chunk_size: int = Field(default=900, ge=200, le=4000)
    chunk_overlap: int = Field(default=120, ge=0, le=1000)


class RagIndexResponse(BaseModel):
    collection: str
    doc_id: str
    chunks_indexed: int


class RagAskRequest(BaseModel):
    question: str = Field(..., min_length=1)
    model: str = Field(default="general", pattern="^(general|coder)$")
    collection: str = "rag-docs"
    top_k: int = Field(default=8, ge=1, le=64)
    page_k: int = Field(default=6, ge=2, le=20)
    temperature: float = Field(default=0.4, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=32, le=8192)
    service: str | None = None
    session_id: str | None = None


class RagSource(BaseModel):
    doc_id: str
    chunk_index: int
    score: float
    text: str
    url: str = ""
    collection: str = ""


class RagAskResponse(BaseModel):
    model: str
    answer: str
    sources: list[RagSource]
    tokens: int = 0


class QueryTelemetryRequest(BaseModel):
    timestamp: str


class QueryTelemetryResponse(BaseModel):
    tokens: float = 0.0
    gpu_cache: float = 0.0
    vram_used_mb: int = 0
    vram_free_mb: int = 0
    cpu_vllm: float = 0.0
    cpu_api: float = 0.0
    ram_vllm: float = 0.0
    ram_api: float = 0.0


class TriggerIngestorRequest(BaseModel):
    source: str = Field(..., pattern="^(aws|azure|kubernetes)$")


class TriggerIngestorResponse(BaseModel):
    source: str
    cronjob: str
    job_name: str


def _base_url(model: str) -> str:
    # All LLM requests now route to the specialised coder model
    return os.getenv("VLLM_CODER_BASE_URL", "http://vllm-coder.ai-platform.svc.cluster.local:8000/v1")


def _redis_client() -> redis.Redis:
    url = os.getenv("REDIS_URL", "redis://redis.ai-platform.svc.cluster.local:6379")
    return redis.from_url(url, decode_responses=True)


async def _get_history(session_id: str | None) -> list[dict]:
    if not session_id:
        return []
    try:
        r = _redis_client()
        data = await r.lrange(f"chat_history:{session_id}", 0, -1)
        return [json.loads(x) for x in data]
    except Exception as e:
        print(f"Redis error: {e}")
        return []


async def _add_history(session_id: str | None, user_text: str, assistant_text: str) -> None:
    if not session_id:
        return
    try:
        r = _redis_client()
        key = f"chat_history:{session_id}"
        await r.rpush(key, json.dumps({"role": "user", "content": user_text}))
        await r.rpush(key, json.dumps({"role": "assistant", "content": assistant_text}))
        await r.ltrim(key, -10, -1)
        await r.expire(key, 86400)
    except Exception as e:
        print(f"Redis error: {e}")


def _model_id(model: str) -> str:
    # Return the specialised coder model ID, defaulting to the 7B Instruct version
    return os.getenv("VLLM_CODER_MODEL_ID", "Qwen/Qwen2.5-Coder-3B-Instruct")


def _embedding_base_url() -> str:
    return os.getenv("EMBEDDING_BASE_URL", "http://embedding-api.ai-platform.svc.cluster.local:80")


def _qdrant_url() -> str:
    return os.getenv("QDRANT_URL", "http://qdrant.ai-platform.svc.cluster.local:6333")


def _infer_service(collection: str, question: str, explicit: str | None) -> str | None:
    if explicit:
        return explicit.strip().lower()
    q = f" {question.lower()} "
    candidates = [
        "fsx", "s3", "ec2", "eks", "ecs", "iam", "lambda", "rds", "dynamodb",
        "cloudfront", "route53", "vpc", "sqs", "sns", "kinesis", "glue", "athena",
    ]
    for c in candidates:
        if f" {c} " in q:
            return c
    if collection == "docs-aws" and "aws " in q:
        return None
    if collection == "docs-azure" and "azure " in q:
        return None
    return None


def _query_terms(question: str) -> list[str]:
    words = re.findall(r"[a-zA-Z0-9-]+", question.lower())
    stop = {"the", "and", "for", "with", "what", "when", "where", "which", "about", "from", "into", "that", "this", "are", "can", "use"}
    return [w for w in words if len(w) > 2 and w not in stop][:12]


def _tei_url() -> str:
    return os.getenv("TEI_URL", "http://reranker.ai-platform.svc.cluster.local:80")


async def _rerank_hits(client: httpx.AsyncClient, question: str, hits: list[dict], top_k: int) -> list[dict]:
    if not hits:
        return []
    
    # Extract the payload texts for reranking
    texts = []
    for h in hits:
        payload = h.get("payload", {})
        # Give the reranker enough context to understand what the chunk is
        text_repr = str(payload.get("title", "")) + "\n" + str(payload.get("text", ""))
        texts.append(text_repr)
        
    try:
        resp = await client.post(
            f"{_tei_url()}/rerank",
            json={
                "query": question,
                "texts": texts,
                "raw_scores": False,
                "return_text": False
            },
            timeout=10.0
        )
        if resp.status_code == 200:
            results = resp.json()
            # TEI returns a list of dictionaries with 'index' and 'score', already sorted by score
            # Example: [{"index": 0, "score": 0.99}, {"index": 5, "score": 0.85}]
            reranked_hits = []
            for item in results[:top_k]:
                idx = item.get("index")
                if idx is not None and idx < len(hits):
                    hit = hits[idx].copy()
                    hit["score"] = item.get("score", 0.0) # overwrite Qdrant dot-product with TEI semantic score
                    reranked_hits.append(hit)
            return reranked_hits
    except Exception as e:
        print(f"TEI Reranker failed: {e}. Falling back to default Qdrant sorting.")
        
    # Fallback if TEI fails: standard dot-product sorting
    return sorted(hits, key=lambda h: float(h.get("score", 0.0)), reverse=True)[:top_k]


async def _qdrant_search(client: httpx.AsyncClient, collection: str, vector: list[float], limit: int, query_filter: dict | None) -> list[dict]:
    payload = {"vector": vector, "limit": limit, "with_payload": True}
    if query_filter:
        payload["filter"] = query_filter
    resp = await client.post(f"{_qdrant_url()}/collections/{collection}/points/search", json=payload)
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail=f"collection '{collection}' not found; run ingestor first")
    resp.raise_for_status()
    return resp.json().get("result", [])


def _chunk_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    cleaned = " ".join(text.split())
    if len(cleaned) <= chunk_size:
        return [cleaned]
    chunks: list[str] = []
    step = max(1, chunk_size - chunk_overlap)
    i = 0
    while i < len(cleaned):
        part = cleaned[i : i + chunk_size].strip()
        if part:
            chunks.append(part)
        i += step
    return chunks


async def _embed_texts(client: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
    emb_resp = await client.post(
        f"{_embedding_base_url()}/v1/embeddings",
        json={"input": texts, "model": "BAAI/bge-m3"},
    )
    emb_resp.raise_for_status()
    data = emb_resp.json().get("data", [])
    vectors = [item.get("embedding", []) for item in data]
    if not vectors or not vectors[0]:
        raise HTTPException(status_code=502, detail="embedding service returned empty vectors")
    return vectors


async def _ensure_collection(client: httpx.AsyncClient, collection: str, dim: int) -> None:
    coll = await client.get(f"{_qdrant_url()}/collections/{collection}")
    if coll.status_code == 200:
        return
    if coll.status_code != 404:
        raise HTTPException(status_code=502, detail=f"qdrant status error: {coll.status_code}")
    create_resp = await client.put(
        f"{_qdrant_url()}/collections/{collection}",
        json={"vectors": {"size": dim, "distance": "Cosine"}},
    )
    create_resp.raise_for_status()


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "agent-api", "status": "ok"}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "healthy"}


@app.get("/readyz")
def readyz() -> dict[str, str]:
    return {"status": "ready"}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    messages = await _get_history(req.session_id)
    messages.append({"role": "user", "content": req.prompt})

    payload = {
        "model": _model_id(req.model),
        "messages": messages,
        "temperature": req.temperature,
        "max_tokens": req.max_tokens,
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{_base_url(req.model)}/chat/completions", json=payload)
            resp.raise_for_status()
            body = resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"upstream status error: {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"upstream connection error: {exc}") from exc

    choices = body.get("choices", [])
    content = choices[0].get("message", {}).get("content", "") if choices else ""
    if content:
        await _add_history(req.session_id, req.prompt, content)
    return ChatResponse(model=req.model, text=content)


@app.post("/rag/index", response_model=RagIndexResponse)
async def rag_index(req: RagIndexRequest) -> RagIndexResponse:
    doc_id = req.doc_id or str(uuid.uuid4())
    chunks = _chunk_text(req.text, req.chunk_size, req.chunk_overlap)
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            vectors = await _embed_texts(client, chunks)
            await _ensure_collection(client, req.collection, len(vectors[0]))
            points = []
            for idx, (chunk, vector) in enumerate(zip(chunks, vectors)):
                points.append(
                    {
                        "id": uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}:{idx}").hex,
                        "vector": vector,
                        "payload": {
                            "doc_id": doc_id,
                            "chunk_index": idx,
                            "text": chunk,
                            "metadata": req.metadata,
                        },
                    }
                )
            upsert_resp = await client.put(
                f"{_qdrant_url()}/collections/{req.collection}/points?wait=true",
                json={"points": points},
            )
            upsert_resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"upstream status error: {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"upstream connection error: {exc}") from exc
    return RagIndexResponse(collection=req.collection, doc_id=doc_id, chunks_indexed=len(chunks))


async def _determine_collections_and_model(client: httpx.AsyncClient, question: str, default_model: str) -> tuple[list[str], str]:
    sys_prompt = (
        "You are an expert routing agent. Based on the user's question, determine two things:\n"
        "1. Which knowledge bases are needed to answer it. Available: 'docs-aws-git', 'docs-azure-git', 'docs-kubernetes-git'.\n"
        "2. Which model should generate the final answer. Available: 'general' (for architecture, definitions, platform facts), 'coder' (if the user is explicitly asking to write, generate, or review raw code).\n"
        "Output ONLY a JSON object containing 'collections' (list of strings) and 'model' (string). Example: {\"collections\": [\"docs-aws-git\", \"docs-kubernetes-git\"], \"model\": \"general\"}\n"
        "Do not include markdown or explanations."
    )
    try:
        resp = await client.post(
            f"{_base_url('general')}/chat/completions",
            json={
                "model": _model_id('general'),
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": question}
                ],
                "temperature": 0.0,
                "max_tokens": 100,
            },
        )
        resp.raise_for_status()
        content = resp.json().get("choices", [])[0].get("message", {}).get("content", "").strip()
        content = content.replace("```json", "").replace("```", "").strip()
        data = json.loads(content)
        
        cols = data.get("collections", [])
        if not isinstance(cols, list):
            cols = ["rag-docs"]
        else:
            cols = [c for c in cols if c in ("docs-aws-git", "docs-azure-git", "docs-kubernetes-git", "rag-docs")]
            
        target_model = data.get("model", default_model)
        if target_model not in ("general", "coder"):
            target_model = default_model
            
        return cols, target_model
    except Exception:
        return ["docs-aws-git", "docs-azure-git", "docs-kubernetes-git"], default_model


@app.post("/rag/ask", response_model=RagAskResponse)
async def rag_ask(req: RagAskRequest) -> RagAskResponse:
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            query_vec = (await _embed_texts(client, [req.question]))[0]
            
            target_model = req.model
            if req.collection == "auto":
                collections_to_search, routed_model = await _determine_collections_and_model(client, req.question, req.model)
                if not collections_to_search:
                    collections_to_search = ["docs-aws-git", "docs-azure-git", "docs-kubernetes-git"]
                # Override the model if the user didn't explicitly demand 'coder'
                if req.model == "general" and routed_model == "coder":
                    target_model = "coder"
            else:
                collections_to_search = [req.collection]

            terms = _query_terms(req.question)
            all_chunk_hits = []
            
            for coll in collections_to_search:
                try:
                    service = _infer_service(coll, req.question, req.service)
                    summary_must = [{"key": "doc_type", "match": {"value": "summary"}}]
                    if service and coll in {"docs-aws-git", "docs-azure-git"}:
                        summary_must.append({"key": "service", "match": {"value": service}})
                    
                    summary_hits = await _qdrant_search(client, coll, query_vec, max(req.page_k * 8, 40), {"must": summary_must})
                    if not summary_hits and len(summary_must) > 1:
                        summary_hits = await _qdrant_search(client, coll, query_vec, max(req.page_k * 8, 40), {"must": [{"key": "doc_type", "match": {"value": "summary"}}]})
                        
                    summary_hits = sorted(summary_hits, key=lambda h: float(h.get("score", 0.0)), reverse=True)
                    selected_urls: list[str] = []
                    for h in summary_hits:
                        u = str(h.get("payload", {}).get("url", "")).strip()
                        if u and u not in selected_urls:
                            selected_urls.append(u)
                        if len(selected_urls) >= req.page_k:
                            break
                            
                    coll_chunk_hits: list[dict] = []
                    for u in selected_urls:
                        page_hits = await _qdrant_search(client, coll, query_vec, max(3, req.top_k), {"must": [{"key": "doc_type", "match": {"value": "chunk"}}, {"key": "url", "match": {"value": u}}]})
                        for h in page_hits:
                            h["__collection_name__"] = coll
                        coll_chunk_hits.extend(page_hits)
                        
                    if not coll_chunk_hits:
                        fallback_must = [{"key": "doc_type", "match": {"value": "chunk"}}]
                        if service and coll in {"docs-aws-git", "docs-azure-git"}:
                            fallback_must.append({"key": "service", "match": {"value": service}})
                        coll_chunk_hits = await _qdrant_search(client, coll, query_vec, max(req.top_k * 8, 20), {"must": fallback_must})
                        for h in coll_chunk_hits:
                            h["__collection_name__"] = coll
                        
                    all_chunk_hits.extend(coll_chunk_hits)
                except Exception:
                    continue

            # Phase 2: Cross-Encoder Reranking
            hits = await _rerank_hits(client, req.question, all_chunk_hits, req.top_k)
            
            sources: list[RagSource] = []
            context_parts: list[str] = []
            for hit in hits:
                payload = hit.get("payload", {})
                coll_name = hit.get("__collection_name__", "unknown")
                text = payload.get("text", "")
                if text:
                    context_parts.append(text)
                sources.append(
                    RagSource(
                        doc_id=str(payload.get("doc_id", "")),
                        chunk_index=int(payload.get("chunk_index", 0)),
                        score=float(hit.get("score", 0.0)),
                        text=text,
                        url=str(payload.get("url", "")),
                        collection=coll_name,
                    )
                )

            if not context_parts:
                raise HTTPException(status_code=404, detail=f"no context found in collection '{req.collection}'")

            messages = await _get_history(req.session_id)
            
            sys_prompt = (
                "You are an elite Cloud Architect and Kubernetes Platform Engineer assisting an administrator with their homelab. "
                "The user runs a bare-metal homelab utilizing GitOps (ArgoCD), an LGTM observability stack (Prometheus, Mimir, Tempo, Grafana), "
                "vLLM AI GPU workers, and Qdrant. Consider this architecture when giving advice.\n\n"
                "When asked to compare technologies, evaluate designs, or provide summaries:\n"
                "1. ALWAYS structure your output using rich Markdown.\n"
                "2. Use comprehensive Tables to benchmark features side-by-side.\n"
                "3. Break down 'Key Architectural Differences' and 'Operational Experience'.\n"
                "4. End with a customized 'Recommendation' tailored specifically to the user's homelab stack.\n\n"
                "Use the following retrieved institutional documentation to ground your facts, but confidently use your general expert knowledge to "
                "fill in gaps, structure the narrative, and provide deep reasoning.\n\n"
                "Context:\n" + chr(10).join(context_parts)
            )
            
            final_messages = [{"role": "system", "content": sys_prompt}]
            final_messages.extend(messages)
            final_messages.append({"role": "user", "content": req.question})

            llm_resp = await client.post(
                f"{_base_url(target_model)}/chat/completions",
                json={
                    "model": _model_id(target_model),
                    "messages": final_messages,
                    "temperature": req.temperature,
                    "max_tokens": req.max_tokens,
                },
            )
            llm_resp.raise_for_status()
            body = llm_resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"upstream status error: {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"upstream connection error: {exc}") from exc

    choices = body.get("choices", [])
    answer = choices[0].get("message", {}).get("content", "") if choices else ""
    tokens_used = body.get("usage", {}).get("total_tokens", 0)
    if answer:
        await _add_history(req.session_id, req.question, answer)
    return RagAskResponse(model=target_model, answer=answer, sources=sources, tokens=tokens_used)


@app.post("/ops/query-telemetry", response_model=QueryTelemetryResponse)
async def ops_query_telemetry(req: QueryTelemetryRequest):
    prometheus_url = "http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090/api/v1/query"
    headers = {"X-Scope-OrgID": "anonymous"}
    resp_data = QueryTelemetryResponse()
    
    async with httpx.AsyncClient(timeout=5.0) as client:
        # tokens (bypassed, now fetched synchronously via vLLM)
        resp_data.tokens = 0.0
            
        # gpu_cache
        q_gpu = "avg(vllm:gpu_cache_usage_perc) * 100"
        r = await client.get(prometheus_url, params={"query": q_gpu})
        if r.status_code == 200:
            res = r.json().get("data", {}).get("result", [])
            if res:
                resp_data.gpu_cache = float(res[0]["value"][1])
                
        # exact vram
        q_vram_used = "sum(DCGM_FI_DEV_FB_USED)"
        r_used = await client.get(prometheus_url, params={"query": q_vram_used})
        if r_used.status_code == 200:
            res_u = r_used.json().get("data", {}).get("result", [])
            if res_u:
                resp_data.vram_used_mb = int(float(res_u[0]["value"][1]))
                
        q_vram_free = "sum(DCGM_FI_DEV_FB_FREE)"
        r_free = await client.get(prometheus_url, params={"query": q_vram_free})
        if r_free.status_code == 200:
            res_f = r_free.json().get("data", {}).get("result", [])
            if res_f:
                resp_data.vram_free_mb = int(float(res_f[0]["value"][1]))
            
        # cpu
        q_cpu = 'sum(rate(container_cpu_usage_seconds_total{namespace="ai-platform", pod=~"vllm.*|agent-api.*"}[5m])) by (pod)'
        r = await client.get(prometheus_url, params={"query": q_cpu})
        if r.status_code == 200:
            for item in r.json().get("data", {}).get("result", []):
                pod = item.get("metric", {}).get("pod", "")
                val = float(item.get("value", [0, 0])[1])
                if "vllm" in pod:
                    resp_data.cpu_vllm += val
                elif "agent-api" in pod:
                    resp_data.cpu_api += val
                    
        # ram
        q_ram = 'sum(container_memory_working_set_bytes{namespace="ai-platform", pod=~"vllm.*|agent-api.*"}) by (pod) / 1024 / 1024'
        r = await client.get(prometheus_url, params={"query": q_ram})
        if r.status_code == 200:
            for item in r.json().get("data", {}).get("result", []):
                pod = item.get("metric", {}).get("pod", "")
                val = float(item.get("value", [0, 0])[1])
                if "vllm" in pod:
                    resp_data.ram_vllm += val
                elif "agent-api" in pod:
                    resp_data.ram_api += val

    return resp_data


@app.get("/ops/status")
async def ops_status() -> dict:
    _ensure_k8s()
    namespace = "ai-platform"
    core = k8s_client.CoreV1Api()
    apps_api = k8s_client.AppsV1Api()
    batch = k8s_client.BatchV1Api()
    custom = k8s_client.CustomObjectsApi()

    pods = core.list_namespaced_pod(namespace).items
    deployments = apps_api.list_namespaced_deployment(namespace).items
    statefulsets = apps_api.list_namespaced_stateful_set(namespace).items
    cronjobs = batch.list_namespaced_cron_job(namespace).items
    jobs = batch.list_namespaced_job(namespace).items

    pod_rows = []
    for p in pods:
        restarts = 0
        for cs in p.status.container_statuses or []:
            restarts += cs.restart_count
        pod_rows.append(
            {
                "name": p.metadata.name,
                "status": p.status.phase,
                "node": p.spec.node_name,
                "restarts": restarts,
            }
        )

    deploy_rows = []
    for d in deployments:
        desired = d.spec.replicas or 0
        ready = d.status.ready_replicas or 0
        deploy_rows.append({"name": d.metadata.name, "ready": ready, "desired": desired})

    sts_rows = []
    for s in statefulsets:
        desired = s.spec.replicas or 0
        ready = s.status.ready_replicas or 0
        sts_rows.append({"name": s.metadata.name, "ready": ready, "desired": desired})

    cj_rows = []
    for cj in cronjobs:
        cj_rows.append(
            {
                "name": cj.metadata.name,
                "schedule": cj.spec.schedule,
                "suspend": bool(cj.spec.suspend),
                "last_schedule_time": cj.status.last_schedule_time.isoformat() if cj.status and cj.status.last_schedule_time else None,
            }
        )

    job_rows = []
    for j in sorted(jobs, key=lambda x: x.metadata.creation_timestamp or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:20]:
        job_rows.append(
            {
                "name": j.metadata.name,
                "succeeded": j.status.succeeded or 0,
                "failed": j.status.failed or 0,
                "active": j.status.active or 0,
                "start_time": j.status.start_time.isoformat() if j.status and j.status.start_time else None,
            }
        )

    argo_apps = []
    try:
        raw = custom.list_namespaced_custom_object(
            group="argoproj.io",
            version="v1alpha1",
            namespace="argocd",
            plural="applications",
        )
        for item in raw.get("items", []):
            spec = item.get("spec", {})
            if spec.get("project") == "ai-platform":
                status = item.get("status", {})
                argo_apps.append(
                    {
                        "name": item.get("metadata", {}).get("name"),
                        "sync": status.get("sync", {}).get("status", "Unknown"),
                        "health": status.get("health", {}).get("status", "Unknown"),
                    }
                )
    except ApiException:
        argo_apps = []

    qdrant_collections = []
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            q = await client.get(f"{_qdrant_url()}/collections")
            if q.status_code == 200:
                qdrant_collections = [c.get("name") for c in q.json().get("result", {}).get("collections", [])]
    except Exception:
        qdrant_collections = []

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pods": pod_rows,
        "deployments": deploy_rows,
        "statefulsets": sts_rows,
        "cronjobs": cj_rows,
        "jobs": job_rows,
        "argocd_apps": argo_apps,
        "qdrant_collections": qdrant_collections,
    }


@app.post("/ops/actions/run-ingestor", response_model=TriggerIngestorResponse)
def run_ingestor(req: TriggerIngestorRequest) -> TriggerIngestorResponse:
    _ensure_k8s()
    batch = k8s_client.BatchV1Api()
    namespace = "ai-platform"
    cronjob_name = "aws-git-ingestor"
    if req.source == "azure":
        cronjob_name = "azure-git-ingestor"
    elif req.source == "kubernetes":
        cronjob_name = "kubernetes-git-ingestor"

    try:
        cronjob = batch.read_namespaced_cron_job(name=cronjob_name, namespace=namespace)
        job = k8s_client.V1Job(
            metadata=k8s_client.V1ObjectMeta(generate_name=f"{cronjob_name}-manual-"),
            spec=cronjob.spec.job_template.spec,
        )
        created = batch.create_namespaced_job(namespace=namespace, body=job)
    except ApiException as exc:
        raise HTTPException(status_code=500, detail=f"k8s api error: {exc.reason}") from exc

    return TriggerIngestorResponse(source=req.source, cronjob=cronjob_name, job_name=created.metadata.name)
