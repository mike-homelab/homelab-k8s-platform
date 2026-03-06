from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
import requests


app = FastAPI(title="vLLM Code Service", version=os.getenv("APP_VERSION", "0.1.0"))
VLLM_BASE = os.getenv("VLLM_BASE_URL", "http://vllm-llm.ai-platform.svc.cluster.local:8000/v1")
MODEL = os.getenv("VLLM_MODEL", "Qwen/Qwen2.5-3B-Instruct")


class PromptRequest(BaseModel):
    prompt: str
    temperature: float = 0.2


@app.get("/health")
def health():
    return {"status": "ok", "vllm_base": VLLM_BASE, "model": MODEL}


@app.post("/generate")
def generate(req: PromptRequest):
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You are a coding assistant."},
            {"role": "user", "content": req.prompt},
        ],
        "temperature": req.temperature,
    }
    try:
        r = requests.post(f"{VLLM_BASE}/chat/completions", json=payload, timeout=120)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"vLLM call failed: {e}")
    return data


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
