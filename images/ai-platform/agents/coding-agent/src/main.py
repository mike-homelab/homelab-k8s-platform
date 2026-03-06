from fastapi import FastAPI
from pydantic import BaseModel
import os


app = FastAPI(
    title="Coding Agent",
    version=os.getenv("APP_VERSION", "0.1.0"),
)


class SuggestRequest(BaseModel):
    prompt: str
    language: str = "python"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    return {"ready": True}


@app.post("/suggest")
def suggest(req: SuggestRequest):
    # Minimal deterministic output for early integration testing.
    suggestion = (
        f"Language: {req.language}\n"
        "1. Create a small reproducible function.\n"
        "2. Add input validation and clear errors.\n"
        "3. Add unit tests for happy path and edge cases.\n"
        f"Prompt summary: {req.prompt[:200]}"
    )
    return {"suggestion": suggestion}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
