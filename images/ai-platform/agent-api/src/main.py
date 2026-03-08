import os
import uuid

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(title="agent-api", version="0.3.1")

cors_allow_origins = [x.strip() for x in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allow_origins if cors_allow_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=512, ge=32, le=2048)


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
    content = ""
    if choices:
        content = choices[0].get("message", {}).get("content", "")
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
            search_resp = await client.post(
                f"{_qdrant_url()}/collections/{req.collection}/points/search",
                json={"vector": query_vec, "limit": req.top_k, "with_payload": True},
            )
            search_resp.raise_for_status()
            hits = search_resp.json().get("result", [])
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
    answer = ""
    if choices:
        answer = choices[0].get("message", {}).get("content", "")
    return RagAskResponse(model=req.model, answer=answer, sources=sources)
