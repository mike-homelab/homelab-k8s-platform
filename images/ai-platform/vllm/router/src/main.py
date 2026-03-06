from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
import requests


app = FastAPI(title="vLLM Router Service", version=os.getenv("APP_VERSION", "0.1.0"))
PLANNER_URL = os.getenv("PLANNER_URL", "http://vllm-planner:8080")
CODE_URL = os.getenv("CODE_URL", "http://vllm-code:8080")


class RouteRequest(BaseModel):
    prompt: str
    temperature: float = 0.2


@app.get("/health")
def health():
    return {"status": "ok", "planner_url": PLANNER_URL, "code_url": CODE_URL}


@app.post("/route")
def route(req: RouteRequest):
    planner_keywords = ("plan", "strategy", "steps", "roadmap")
    target = "planner" if any(k in req.prompt.lower() for k in planner_keywords) else "code"
    return {"target": target}


@app.post("/chat")
def chat(req: RouteRequest):
    target = route(req)["target"]
    try:
        if target == "planner":
            r = requests.post(
                f"{PLANNER_URL}/plan",
                json={"goal": req.prompt, "max_steps": 5},
                timeout=60,
            )
        else:
            r = requests.post(
                f"{CODE_URL}/generate",
                json={"prompt": req.prompt, "temperature": req.temperature},
                timeout=120,
            )
        r.raise_for_status()
        return {"target": target, "response": r.json()}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"backend call failed: {e}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
