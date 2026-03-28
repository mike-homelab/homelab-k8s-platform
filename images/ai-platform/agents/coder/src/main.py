from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx
import os

app = FastAPI(title="Coder Agent API", description="Specialized coding subagent.")

class TaskRequest(BaseModel):
    prompt: str

class TaskResponse(BaseModel):
    result: str

VLLM_API_URL = os.getenv("VLLM_API_URL", "http://vllm-coder:8000/v1/chat/completions")

@app.post("/task", response_model=TaskResponse)
async def handle_task(request: TaskRequest):
    payload = {
        "model": "casperhansen/llama-3-70b-instruct-awq",
        "messages": [
            {"role": "system", "content": "You are a senior software engineer specialized in reading and writing code, identifying bugs, and refactoring applications. Your responses should prioritize code quality and architectural best practices."},
            {"role": "user", "content": request.prompt}
        ],
        "temperature": 0.2,
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
