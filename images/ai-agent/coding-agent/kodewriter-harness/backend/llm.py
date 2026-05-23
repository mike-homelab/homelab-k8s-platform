import os
import json
import requests
from typing import Optional, List, Dict, Any

LITELLM_BASE = os.getenv("LITELLM_BASE", "https://llm.michaelhomelab.work/v1")
LITELLM_KEY = os.getenv("LITELLM_KEY", "sk-michael-homelab-llm-proxy")

MODEL_PLANNER = "analyst"  # Qwen3-14B
MODEL_CODER = "builder"    # Qwen2.5-Coder-14B

# MCP server configuration
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8080/sse")
MCP_SERVER_TIMEOUT = 30

from langfuse import Langfuse

# Initialize Langfuse
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY")
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "http://langfuse.ai-platform.svc:3000")

langfuse_client = None
if LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY:
    langfuse_client = Langfuse(
        public_key=LANGFUSE_PUBLIC_KEY,
        secret_key=LANGFUSE_SECRET_KEY,
        host=LANGFUSE_HOST
    )

def llm_call(model: str, system: str, user: str, temperature: float = 0.2,
             max_tokens: int = 8192, stream: bool = False, parent: Any = None) -> str:
    """Send a chat completion request to the LiteLLM proxy with Langfuse tracing."""
    url = f"{LITELLM_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {LITELLM_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "temperature": max(temperature, 0.1),
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": stream
    }
    
    if langfuse_client:
        # v2 Syntax: Use generation() on parent (trace) or start a new trace
        generation = None
        if parent:
            generation = parent.generation(
                name=f"llm-call-{model}",
                input={"system": system, "user": user},
                model=model,
                metadata={"temperature": temperature}
            )
        else:
            # Fallback if no parent provided
            trace = langfuse_client.trace(name=f"orphan-llm-call-{model}")
            generation = trace.generation(
                name=f"llm-call-{model}",
                input={"system": system, "user": user},
                model=model,
                metadata={"temperature": temperature}
            )
            
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=300, verify=False)
            resp.raise_for_status()
            result = resp.json()
            content = result["choices"][0]["message"]["content"].strip()
            
            generation.update(
                output=content,
                usage={
                    "prompt_tokens": result.get("usage", {}).get("prompt_tokens"),
                    "completion_tokens": result.get("usage", {}).get("completion_tokens"),
                    "total_tokens": result.get("usage", {}).get("total_tokens")
                }
            )
            langfuse_client.flush()
            return content
        except Exception as e:
            generation.update(level="ERROR", status_message=str(e))
            langfuse_client.flush()
            print(f"LLM call failed for {model}: {e}")
            raise RuntimeError(f"LLM call failed: {e}")
    else:
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=300, verify=False)
            resp.raise_for_status()
            result = resp.json()
            return result["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"LLM call failed for {model}: {e}")
            raise RuntimeError(f"LLM call failed: {e}")

PERCEPTION_BASE = os.getenv("PERCEPTION_BASE", "http://perception.ai-platform.svc:8000")
RERANKER_BASE = os.getenv("RERANKER_BASE", "http://perception.ai-platform.svc:8001")

def get_embedding(text: str) -> List[float]:
    """Get embeddings from Perception API (GTE-Qwen2-1.5B)."""
    url = f"{PERCEPTION_BASE}/v1/embeddings"
    payload = {
        "model": "Alibaba-NLP/gte-Qwen2-1.5B-instruct",
        "input": text
    }
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]

def rerank(query: str, documents: List[str], top_n: int = 5) -> List[Dict[str, Any]]:
    """Rerank documents using Perception API (GTE-Qwen2-1.5B)."""
    url = f"{RERANKER_BASE}/rerank"
    payload = {
        "model": "Alibaba-NLP/gte-Qwen2-1.5B-instruct",
        "query": query,
        "documents": documents,
        "top_n": top_n
    }
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()["results"]

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://searxng.ai-platform.svc:8080")

def web_search(query: str, limit: int = 5) -> List[str]:
    """Search the web using SearxNG."""
    try:
        resp = requests.get(
            f"{SEARXNG_URL}/search",
            params={"q": query, "format": "json"},
            timeout=15
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return [r.get("content", r.get("title", "")) for r in results[:limit]]
    except Exception as e:
        print(f"Web search failed: {e}")
        return []

def planner_call(prompt: str, context: str = "", parent: Any = None) -> str:
    # Use reasoning model for planning
    system = "You are the Kodewriter Planner (Analyst). Decompose the user request into a step-by-step execution plan. Use reasoning and reflection."
    user = f"Context:\n{context}\n\nRequest: {prompt}"
    return llm_call(MODEL_PLANNER, system, user, parent=parent)

def coder_call(prompt: str, context: str = "", parent: Any = None) -> str:
    # Use coding model for patch generation
    system = "You are the Kodewriter Coder (Builder). Generate precise code patches or solutions. Follow the plan strictly."
    user = f"Context:\n{context}\n\nTask: {prompt}"
    return llm_call(MODEL_CODER, system, user, parent=parent)
