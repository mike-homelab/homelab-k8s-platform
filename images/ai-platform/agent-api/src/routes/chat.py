import json
import uuid
from typing import AsyncGenerator, Optional
import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from ..models import (
    ChatRequest, ChatResponse, RagIndexRequest, RagIndexResponse,
    RagAskRequest, RagAskResponse, RagSource, OAIChatRequest
)
from ..config import _base_url, _model_id
from ..utils import (
    _get_history, _add_history, _chunk_text, _embed_texts, _ensure_collection,
    _determine_collections_and_model, _query_terms, _qdrant_search, _infer_service, _rerank_hits
)

router = APIRouter()

@router.post("/chat", response_model=ChatResponse)
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


@router.post("/rag/index", response_model=RagIndexResponse)
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
                f"{_base_url('')}/../points?wait=true".replace("/v1", "") if False else f"{_qdrant_url()}/collections/{req.collection}/points?wait=true", # Wait Qdrant URL is accessible
                json={"points": points},
            )
            upsert_resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"upstream status error: {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"upstream connection error: {exc}") from exc
    return RagIndexResponse(collection=req.collection, doc_id=doc_id, chunks_indexed=len(chunks))


@router.post("/rag/ask", response_model=RagAskResponse)
async def rag_ask(req: RagAskRequest) -> RagAskResponse:
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            query_vec = (await _embed_texts(client, [req.question]))[0]
            
            target_model = req.model
            if req.collection == "auto":
                _, routed_model = await _determine_collections_and_model(client, req.question, req.model)
                collections_to_search = ["docs-aws-git", "docs-azure-git", "docs-kubernetes-git", "rag-docs"]
                if req.model == "general" and routed_model == "coder":
                    target_model = "coder"
            else:
                collections_to_search = [req.collection]

            terms = _query_terms(req.question)
            all_chunk_hits = []
            
            import asyncio
            async def _fetch_web():
                try:
                    def sync_ddg():
                        from duckduckgo_search import DDGS
                        with DDGS() as ddgs:
                            return list(ddgs.text(req.question, max_results=5))
                    res = await asyncio.get_running_loop().run_in_executor(None, sync_ddg)
                    hits = []
                    for i, r in enumerate(res):
                        hits.append({
                            "id": f"web-{i}",
                            "vector": [],
                            "payload": {
                                "text": r.get("body", ""),
                                "url": r.get("href", ""),
                                "doc_id": "web-search",
                                "chunk_index": i
                            },
                            "score": 0.0,
                            "__collection_name__": "web-search"
                        })
                    return hits
                except Exception as e:
                    print(f"Web search failed: {e}")
                    return []
                    
            web_task = asyncio.create_task(_fetch_web())

            for coll in collections_to_search:
                try:
                    service = _infer_service(coll, req.question, req.service)
                    
                    if "git" in coll:
                        coll_chunk_hits = await _qdrant_search(client, coll, query_vec, 60, None)
                        for h in coll_chunk_hits:
                            h["__collection_name__"] = coll
                        all_chunk_hits.extend(coll_chunk_hits)
                        continue

                    # skip long config for briefly
                    fallback_must = [{"key": "doc_type", "match": {"value": "chunk"}}]
                    coll_chunk_hits = await _qdrant_search(client, coll, query_vec, 60, {"must": fallback_must})
                    for h in coll_chunk_hits:
                        h["__collection_name__"] = coll
                    all_chunk_hits.extend(coll_chunk_hits)
                except Exception:
                    continue

            web_hits = await web_task
            all_chunk_hits.extend(web_hits)
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
                context_parts.append("No context found in local knowledge base or web search. Please answer to the best of your ability without external references.")

            messages = await _get_history(req.session_id)
            
            sys_prompt = (
                "You are an elite Cloud Architect and Kubernetes Platform Engineer assisting an administrator with their homelab. "
                "Context:\n" + "\n".join(context_parts)
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

# ---------------- OpenAI Compliant API ----------------

@router.get("/v1/models")
async def list_models() -> JSONResponse:
    import time
    return JSONResponse({
        "object": "list",
        "data": [
            {
                "id": "homelab-rag",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "homelab",
                "description": "RAG-powered assistant",
            }
        ],
    })

async def _rag_stream(answer: str, model: str) -> AsyncGenerator[bytes, None]:
    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    words = answer.split(" ")
    import time
    for i, word in enumerate(words):
        token = word if i == 0 else " " + word
        chunk = {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": token}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(chunk)}\n\n".encode()
    yield b"data: [DONE]\n\n"

@router.post("/v1/chat/completions")
async def oai_chat_completions(req: OAIChatRequest):
    user_messages = [m for m in req.messages if m.role == "user"]
    if not user_messages:
        raise HTTPException(status_code=422, detail="No user message found")
    question = user_messages[-1].content.strip()

    session_id: Optional[str] = None
    for m in req.messages:
        if m.role == "system" and "session_id:" in m.content:
            try: session_id = m.content.split("session_id:")[1].split()[0]; break
            except: pass

    rag_req = RagAskRequest(question=question, model="general", collection="auto", top_k=req.top_k, max_tokens=req.max_tokens, temperature=req.temperature, session_id=session_id)
    rag_resp = await rag_ask(rag_req)
    answer = rag_resp.answer

    if req.stream:
        return StreamingResponse(_rag_stream(answer, req.model), media_type="text/event-stream")

    import time
    return JSONResponse({
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": req.model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": rag_resp.tokens, "total_tokens": rag_resp.tokens}
    })
