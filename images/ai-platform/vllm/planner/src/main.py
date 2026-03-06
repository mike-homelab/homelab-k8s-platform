from fastapi import FastAPI
from pydantic import BaseModel
import os


app = FastAPI(title="vLLM Planner Service", version=os.getenv("APP_VERSION", "0.1.0"))


class PlanRequest(BaseModel):
    goal: str
    max_steps: int = 5


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/plan")
def plan(req: PlanRequest):
    steps = [
        f"Clarify goal: {req.goal[:180]}",
        "Identify required inputs and dependencies",
        "Break into implementation tasks",
        "Define validation and test criteria",
        "Prepare rollout and monitoring checks",
    ]
    return {"goal": req.goal, "steps": steps[: max(1, req.max_steps)]}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
