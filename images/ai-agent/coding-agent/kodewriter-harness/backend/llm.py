import os
import json
import requests
from typing import Optional, List, Dict, Any

LITELLM_BASE = os.getenv("LITELLM_BASE", "https://llm.michaelhomelab.work/v1")
LITELLM_KEY = os.getenv("LITELLM_KEY", "sk-michael-homelab-llm-proxy")

MODEL_PLANNER = "analyst"  # Qwen3-14B
MODEL_CODER = "builder"    # Qwen2.5-Coder-14B

def llm_call(model: str, system: str, user: str, temperature: float = 0.2,
             max_tokens: int = 8192, stream: bool = False) -> str:
    """Send a chat completion request to the LiteLLM proxy."""
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

def planner_call(prompt: str, context: str = "") -> str:
    # Use reasoning model for planning
    system = "You are the Kodewriter Planner (Analyst). Decompose the user request into a step-by-step execution plan. Use reasoning and reflection."
    user = f"Context:\n{context}\n\nRequest: {prompt}"
    return llm_call(MODEL_PLANNER, system, user)

def coder_call(prompt: str, context: str = "") -> str:
    # Use coding model for patch generation
    system = "You are the Kodewriter Coder (Builder). Generate precise code patches or solutions. Follow the plan strictly."
    user = f"Context:\n{context}\n\nTask: {prompt}"
    return llm_call(MODEL_CODER, system, user)
