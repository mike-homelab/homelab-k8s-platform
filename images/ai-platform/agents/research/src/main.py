from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx
import os

app = FastAPI(title="Research Agent API", description="Specialized research subagent.")

class TaskRequest(BaseModel):
    prompt: str

class TaskResponse(BaseModel):
    result: str

VLLM_API_URL = os.getenv("VLLM_API_URL", "http://agent-api:8000/v1/chat/completions")
EMBEDDING_API_URL = os.getenv("EMBEDDING_API_URL", "http://embedding-api:80/query")

@app.post("/task", response_model=TaskResponse)
async def handle_task(request: TaskRequest):
    # Simulated RAG fetching step could be added here
    context_str = ""
    # Usually a research agent might fetch external context or RAG documents here
    
    payload = {
        "model": "casperhansen/llama-3-70b-instruct-awq",
        "messages": [
            {"role": "system", "content": "You are a senior technical researcher. Give detailed analysis, summarize information effectively, and cite your sources or reasoning when making factual claims."},
            {"role": "user", "content": request.prompt}
        ],
        "temperature": 0.5,
        "max_tokens": 4096
    }
    
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            res = await client.post(VLLM_API_URL, json=payload)
            res.raise_for_status()
            data = res.json()
            return TaskResponse(result=data["choices"][0]["message"]["content"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM call failed directly: {e}")

@app.get("/health")
def health_check():
    return {"status": "ok"}
