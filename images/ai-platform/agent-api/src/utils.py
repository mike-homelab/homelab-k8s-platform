import json
import re
import uuid
import httpx
from fastapi import HTTPException
from .config import _redis_client, _tei_url, _qdrant_url, _embedding_base_url, _base_url, _model_id

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


async def _rerank_hits(client: httpx.AsyncClient, question: str, hits: list[dict], top_k: int) -> list[dict]:
    if not hits:
        return []
    
    texts = []
    for h in hits:
        payload = h.get("payload", {})
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
            reranked_hits = []
            for item in results[:top_k]:
                idx = item.get("index")
                if idx is not None and idx < len(hits):
                    hit = hits[idx].copy()
                    hit["score"] = item.get("score", 0.0)
                    reranked_hits.append(hit)
            return reranked_hits
    except Exception as e:
        print(f"TEI Reranker failed: {e}. Falling back to default Qdrant sorting.")
        
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
