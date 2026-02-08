from fastapi import FastAPI, Request
import httpx
import os

from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

app = FastAPI()
FastAPIInstrumentor.instrument_app(app)
HTTPXClientInstrumentor().instrument()

MODEL_ENDPOINTS = {
    "planner": "http://vllm-llama3.ai-platform.svc.cluster.local:8000",
    "code": "http://vllm-mixtral.ai-platform.svc.cluster.local:8000",
    "embedding": "http://vllm-bge-embeddings.ai-platform.svc.cluster.local:8000",
}

@app.post("/v1/chat/completions")
async def route_chat(request: Request):
    body = await request.json()

    model_role = body.get("model", "planner")
    backend = MODEL_ENDPOINTS.get(model_role)

    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(
            f"{backend}/v1/chat/completions",
            json=body
        )
        return resp.json()
