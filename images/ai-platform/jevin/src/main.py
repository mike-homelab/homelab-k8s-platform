from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx
import os
import re

app = FastAPI(title="Jevin Orchestrator API", description="Multi-agent orchestrator for delegating tasks.")

class ChatRequest(BaseModel):
    prompt: str

class ChatResponse(BaseModel):
    response: str
    agent_used: str

# Define subagent URLs
CODER_URL = os.getenv("CODER_AGENT_URL", "http://coder-agent:8000/task")
RESEARCHER_URL = os.getenv("RESEARCH_AGENT_URL", "http://research-agent:8000/task")
VLLM_API_URL = os.getenv("VLLM_API_URL", "http://vllm-coder:8000/v1/chat/completions")

@app.post("/agent/chat", response_model=ChatResponse)
async def agent_chat(request: ChatRequest):
    prompt = request.prompt.lower()
    
    # Simple keyword-based routing for subagents, or we could use the LLM to decide
    # Let's do a simple heuristic first
    if any(word in prompt for word in ["code", "debug", "refactor", "function", "script", "kubernetes", "yaml"]):
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                res = await client.post(CODER_URL, json={"prompt": request.prompt})
                res.raise_for_status()
                data = res.json()
                return ChatResponse(response=data.get("result", "No result"), agent_used="coder-agent")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Coder agent failed: {e}")
            
    elif any(word in prompt for word in ["research", "explain", "summarize", "find", "what is"]):
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                res = await client.post(RESEARCHER_URL, json={"prompt": request.prompt})
                res.raise_for_status()
                data = res.json()
                return ChatResponse(response=data.get("result", "No result"), agent_used="research-agent")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Research agent failed: {e}")

    # Fallback default response from Jevin directly if no specific agent was selected
    # This acts as a generic orchestrator fallback
    try:
        # We can reach out directly to VLLM here
        payload = {
            "model": "casperhansen/llama-3-70b-instruct-awq",
            "messages": [
                {"role": "system", "content": "You are Jevin, the main orchestrator agent. I handle general inquiries and delegate tasks to my subagents when appropriate."},
                {"role": "user", "content": request.prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 1024
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            res = await client.post(VLLM_API_URL, json=payload)
            res.raise_for_status()
            data = res.json()
            return ChatResponse(response=data["choices"][0]["message"]["content"], agent_used="jevin-orchestrator")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM call failed directly: {e}")

@app.get("/health")
def health_check():
    return {"status": "ok"}
