import os

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="agent-api", version="0.2.0")


class ChatRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    model: str = Field(default="general", pattern="^(general|coder)$")
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=256, ge=1, le=2048)


class ChatResponse(BaseModel):
    model: str
    text: str


def _base_url(model: str) -> str:
    if model == "coder":
        return os.getenv("VLLM_CODER_BASE_URL", "http://vllm-coder.ai-platform.svc.cluster.local:8000/v1")
    return os.getenv("VLLM_GENERAL_BASE_URL", os.getenv("VLLM_BASE_URL", "http://vllm-llm.ai-platform.svc.cluster.local:8000/v1"))


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
        "model": "default",
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
