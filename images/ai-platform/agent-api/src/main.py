import os
import re
import uuid
from datetime import datetime, timezone

import httpx
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

app = FastAPI(title="agent-api", version="0.5.0")

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
    top_k: int = Field(default=4, ge=1, le=10)
    page_k: int = Field(default=6, ge=2, le=20)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=512, ge=32, le=2048)
    service: str | None = None


class RagSource(BaseModel):
    doc_id: str
    chunk_index: int
    score: float
    text: str
    url: str = ""


class RagAskResponse(BaseModel):
    model: str
    answer: str
    sources: list[RagSource]


class TriggerIngestorRequest(BaseModel):
    source: str = Field(..., pattern="^(aws|azure)$")


class TriggerIngestorResponse(BaseModel):
    source: str
    cronjob: str
    job_name: str


def _base_url(model: str) -> str:
    if model == "coder":
        return os.getenv("VLLM_CODER_BASE_URL", "http://vllm-coder.ai-platform.svc.cluster.local:8000/v1")
    return os.getenv("VLLM_GENERAL_BASE_URL", os.getenv("VLLM_BASE_URL", "http://vllm-llm.ai-platform.svc.cluster.local:8000/v1"))


def _model_id(model: str) -> str:
    if model == "coder":
        return os.getenv("VLLM_CODER_MODEL_ID", "Qwen/Qwen2.5-Coder-3B-Instruct")
    return os.getenv("VLLM_GENERAL_MODEL_ID", "Qwen/Qwen2.5-3B-Instruct")


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


def _boosted_score(hit: dict, terms: list[str]) -> float:
    base = float(hit.get("score", 0.0))
    payload = hit.get("payload", {})
    text = (
        str(payload.get("title", "")) + " " +
        str(payload.get("url", "")) + " " +
        str(payload.get("summary", "")) + " " +
        str(payload.get("text", "")) + " " +
        " ".join(payload.get("keywords", []) if isinstance(payload.get("keywords"), list) else [])
    ).lower()
    bonus = 0.0
    for t in terms:
        if t in text:
            bonus += 0.03
    return base + min(bonus, 0.24)


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
    payload = {
        "model": _model_id(req.model),
        "messages": [{"role": "user", "content": req.prompt}],
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


@app.post("/rag/ask", response_model=RagAskResponse)
async def rag_ask(req: RagAskRequest) -> RagAskResponse:
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            query_vec = (await _embed_texts(client, [req.question]))[0]
            service = _infer_service(req.collection, req.question, req.service)
            terms = _query_terms(req.question)
            summary_must = [{"key": "doc_type", "match": {"value": "summary"}}]
            if service and req.collection in {"docs-aws", "docs-azure"}:
                summary_must.append({"key": "service", "match": {"value": service}})
            summary_hits = await _qdrant_search(
                client,
                req.collection,
                query_vec,
                max(req.page_k * 8, 40),
                {"must": summary_must},
            )
            if not summary_hits and len(summary_must) > 1:
                summary_hits = await _qdrant_search(
                    client,
                    req.collection,
                    query_vec,
                    max(req.page_k * 8, 40),
                    {"must": [{"key": "doc_type", "match": {"value": "summary"}}]},
                )

            summary_hits = sorted(summary_hits, key=lambda h: _boosted_score(h, terms), reverse=True)
            selected_urls: list[str] = []
            for h in summary_hits:
                u = str(h.get("payload", {}).get("url", "")).strip()
                if u and u not in selected_urls:
                    selected_urls.append(u)
                if len(selected_urls) >= req.page_k:
                    break

            chunk_hits: list[dict] = []
            for u in selected_urls:
                page_hits = await _qdrant_search(
                    client,
                    req.collection,
                    query_vec,
                    max(3, req.top_k),
                    {"must": [
                        {"key": "doc_type", "match": {"value": "chunk"}},
                        {"key": "url", "match": {"value": u}},
                    ]},
                )
                chunk_hits.extend(page_hits)

            if not chunk_hits:
                fallback_must = [{"key": "doc_type", "match": {"value": "chunk"}}]
                if service and req.collection in {"docs-aws", "docs-azure"}:
                    fallback_must.append({"key": "service", "match": {"value": service}})
                chunk_hits = await _qdrant_search(
                    client,
                    req.collection,
                    query_vec,
                    max(req.top_k * 8, 20),
                    {"must": fallback_must},
                )

            hits = sorted(chunk_hits, key=lambda h: _boosted_score(h, terms), reverse=True)[: req.top_k]
            sources: list[RagSource] = []
            context_parts: list[str] = []
            for hit in hits:
                payload = hit.get("payload", {})
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
                    )
                )

            if not context_parts:
                raise HTTPException(status_code=404, detail=f"no context found in collection '{req.collection}'")

            prompt = (
                "Use only the provided context to answer the question. "
                "If the answer is not in context, say you don't know.\n\n"
                f"Context:\n{chr(10).join(context_parts)}\n\nQuestion: {req.question}"
            )
            llm_resp = await client.post(
                f"{_base_url(req.model)}/chat/completions",
                json={
                    "model": _model_id(req.model),
                    "messages": [{"role": "user", "content": prompt}],
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
    return RagAskResponse(model=req.model, answer=answer, sources=sources)


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
    cronjob_name = "aws-docs-ingestor" if req.source == "aws" else "azure-docs-ingestor"

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
